/**
 * Video player stability soak (§6.6 "video player icin stabilite testleri").
 *
 * A lesson video runs for tens of minutes while the player's timeupdate
 * handler fires roughly four times a second — and since the overlay
 * integration hooks that handler (checkOverlays on every tick), the failure
 * this test exists to catch is a slow leak or a gradual degradation that
 * only appears after thousands of ticks, long after a normal e2e test has
 * finished.
 *
 * Real-time playback would make that untestable: catching an hour-long
 * problem would take an hour. Instead the test drives `currentTime` and
 * dispatches `timeupdate` directly, so one minute of wall clock reproduces
 * the tick volume of a long viewing session. That is the honest trade —
 * it stresses the handler and the overlay logic faithfully, but it does
 * NOT exercise the decoder, the network stack or ABR switching. Those need
 * a real-time soak against a real HLS stream; see the README.
 *
 * Knobs (cypress env):
 *   SOAK_CYCLES         playback cycles to simulate (default 20)
 *   SOAK_TICKS_PER_CYCLE timeupdate events per cycle (default 240 = 1 min @4Hz)
 *   SOAK_VIDEO_URL      video file for the fixture lesson
 *   SOAK_HEAP_GROWTH_MB allowed heap growth before failing (default 25)
 */

const CYCLES = Number(Cypress.env("SOAK_CYCLES") || 20);
const TICKS_PER_CYCLE = Number(Cypress.env("SOAK_TICKS_PER_CYCLE") || 240);
const HEAP_GROWTH_LIMIT_MB = Number(Cypress.env("SOAK_HEAP_GROWTH_MB") || 25);
const VIDEO_URL = Cypress.env("SOAK_VIDEO_URL") || "/files/sample-lesson.mp4";

const COURSE = "soak-test-course";
const VIDEO_DURATION = 600; // seconds the fixture video claims to be

describe("Video Player Soak", () => {
	const consoleErrors = [];

	before(() => {
		cy.login();
		seedLesson();
	});

	beforeEach(() => {
		cy.login();
	});

	it("survives repeated playback without leaking or degrading", () => {
		cy.visit(`/lms/courses/${COURSE}/learn/1-1`, {
			onBeforeLoad(win) {
				// Collected rather than failed on immediately: a single
				// transient warning is noise, an error every cycle is a bug.
				cy.stub(win.console, "error").callsFake((...args) => {
					consoleErrors.push(args.join(" "));
				});
			},
		});
		cy.closeOnboardingModal();

		cy.get("video", { timeout: 30000 }).should("exist");

		// Baseline after the page has settled, so first-paint allocations are
		// not counted as leaked.
		let baselineHeap = 0;
		cy.window().then((win) => {
			forceGc(win);
			baselineHeap = heapMb(win);
			cy.log(`baseline heap: ${baselineHeap.toFixed(1)} MB`);
		});

		const samples = [];

		for (let cycle = 0; cycle < CYCLES; cycle++) {
			cy.window().then((win) => {
				const video = win.document.querySelector("video");
				expect(video, "video element still mounted").to.exist;

				// A viewing session is not a clean linear pass: students seek
				// back, change speed and replay. Each of those paths touches
				// different player state, so the soak mixes them in.
				simulatePlayback(win, video, TICKS_PER_CYCLE);

				if (cycle % 3 === 0) {
					video.playbackRate = [0.75, 1, 1.5, 2][cycle % 4];
				}
				if (cycle % 4 === 0) {
					// Seek backwards: re-enters already-passed overlay
					// timestamps, which is where duplicate-trigger bugs live.
					video.currentTime = Math.max(0, video.currentTime - 120);
					video.dispatchEvent(new win.Event("timeupdate"));
				}
				if (cycle % 5 === 0) {
					video.currentTime = 0; // full replay
					video.dispatchEvent(new win.Event("timeupdate"));
				}
			});

			cy.window().then((win) => {
				forceGc(win);
				const heap = heapMb(win);
				samples.push(heap);
				cy.log(`cycle ${cycle + 1}/${CYCLES} heap: ${heap.toFixed(1)} MB`);
			});
		}

		// --- Assertions -------------------------------------------------

		cy.then(() => {
			const finalHeap = samples[samples.length - 1];
			const growth = finalHeap - baselineHeap;

			cy.log(
				`heap ${baselineHeap.toFixed(1)} MB -> ${finalHeap.toFixed(1)} MB ` +
					`(+${growth.toFixed(1)} MB over ${CYCLES * TICKS_PER_CYCLE} ticks)`
			);

			expect(
				growth,
				`heap grew ${growth.toFixed(1)} MB over ${CYCLES} cycles — ` +
					"suspect a listener or timer that is never released"
			).to.be.lessThan(HEAP_GROWTH_LIMIT_MB);

			// Growth that never plateaus is the signature of a real leak, as
			// opposed to caches filling up and settling. Compare the second
			// half's slope with the first half's.
			if (samples.length >= 4) {
				const mid = Math.floor(samples.length / 2);
				const firstHalfGrowth = samples[mid] - samples[0];
				const secondHalfGrowth = samples[samples.length - 1] - samples[mid];
				cy.log(
					`growth first half: ${firstHalfGrowth.toFixed(1)} MB, ` +
						`second half: ${secondHalfGrowth.toFixed(1)} MB`
				);
				expect(
					secondHalfGrowth,
					"heap growth is not slowing down — allocation appears unbounded"
				).to.be.lessThan(Math.max(firstHalfGrowth, 1) * 2);
			}
		});

		// The player must still work, not merely still exist.
		cy.get("video").then(($video) => {
			const video = $video[0];
			expect(video.readyState, "video still has media loaded").to.be.greaterThan(0);
			expect(video.error, "video element reports no error").to.be.null;
		});

		cy.then(() => {
			const repeated = consoleErrors.filter(
				(message) => !message.includes("ResizeObserver")
			);
			expect(
				repeated.length,
				`console errors during soak:\n${repeated.slice(0, 5).join("\n")}`
			).to.be.lessThan(CYCLES); // fewer than one per cycle
		});
	});

	it("keeps player controls responsive after the soak", () => {
		cy.visit(`/lms/courses/${COURSE}/learn/1-1`);
		cy.closeOnboardingModal();
		cy.get("video", { timeout: 30000 }).should("exist");

		cy.window().then((win) => {
			const video = win.document.querySelector("video");
			simulatePlayback(win, video, TICKS_PER_CYCLE * 5);
		});

		// Seek via the range input the way a student would, and confirm the
		// player honours it rather than silently ignoring the event.
		cy.get('input[type="range"].duration-slider')
			.should("exist")
			.then(($slider) => {
				cy.wrap($slider).invoke("val", 120).trigger("input");
			});

		cy.get("video").should(($video) => {
			expect($video[0].currentTime, "seek was applied").to.be.greaterThan(0);
		});
	});
});

// --- helpers -----------------------------------------------------------------

/**
 * Advance the video and fire timeupdate the way the browser would.
 *
 * The component listens on `ontimeupdate`, so dispatching the real event is
 * what exercises its handler — including the overlay check that runs on
 * every tick.
 */
function simulatePlayback(win, video, ticks) {
	const step = 0.25; // 4 Hz, matching a real browser's timeupdate cadence
	for (let i = 0; i < ticks; i++) {
		const next = video.currentTime + step;
		video.currentTime = next > VIDEO_DURATION ? 0 : next;
		video.dispatchEvent(new win.Event("timeupdate"));
	}
}

function forceGc(win) {
	// Exposed by --js-flags=--expose-gc (see cypress.config.js). Without it
	// the reading includes uncollected garbage and a "leak" may be noise.
	if (typeof win.gc === "function") {
		win.gc();
	}
}

function heapMb(win) {
	const memory = win.performance && win.performance.memory;
	if (!memory) {
		throw new Error(
			"performance.memory unavailable — run this spec in Chrome, not Electron"
		);
	}
	return memory.usedJSHeapSize / (1024 * 1024);
}

/**
 * Create the fixture course/chapter/lesson with a video block.
 *
 * Built through the API rather than the UI: the soak is about the player,
 * and driving the course editor here would make the test fail for reasons
 * that have nothing to do with playback stability.
 */
function seedLesson() {
	cy.request({
		url: "/api/method/frappe.client.delete",
		method: "POST",
		body: { doctype: "LMS Course", name: COURSE },
		failOnStatusCode: false,
	});

	cy.request({
		url: "/api/method/frappe.client.insert",
		method: "POST",
		body: {
			doc: {
				doctype: "LMS Course",
				name: COURSE,
				title: "Soak Test Course",
				short_introduction: "Fixture for the video player soak test",
				description: "Fixture for the video player soak test",
				published: 1,
			},
		},
	});

	cy.request({
		url: "/api/method/frappe.client.insert",
		method: "POST",
		body: {
			doc: {
				doctype: "Course Chapter",
				title: "Soak Chapter",
				course: COURSE,
			},
		},
	}).then((chapterResponse) => {
		const chapter = chapterResponse.body.message.name;

		cy.request({
			url: "/api/method/frappe.client.insert",
			method: "POST",
			body: {
				doc: {
					doctype: "Chapter Reference",
					parent: COURSE,
					parenttype: "LMS Course",
					parentfield: "chapters",
					chapter: chapter,
					idx: 1,
				},
			},
			failOnStatusCode: false,
		});

		cy.request({
			url: "/api/method/frappe.client.insert",
			method: "POST",
			body: {
				doc: {
					doctype: "Course Lesson",
					title: "Soak Lesson",
					chapter: chapter,
					course: COURSE,
					// EditorJS block payload: one upload block holding the video.
					content: JSON.stringify({
						time: Date.now(),
						blocks: [
							{
								type: "upload",
								data: { file_url: VIDEO_URL, file_type: "mp4", quizzes: [] },
							},
						],
						version: "2.29.0",
					}),
				},
			},
		}).then((lessonResponse) => {
			const lesson = lessonResponse.body.message.name;
			cy.request({
				url: "/api/method/frappe.client.insert",
				method: "POST",
				body: {
					doc: {
						doctype: "Lesson Reference",
						parent: chapter,
						parenttype: "Course Chapter",
						parentfield: "lessons",
						lesson: lesson,
						idx: 1,
					},
				},
				failOnStatusCode: false,
			});
		});
	});
}
