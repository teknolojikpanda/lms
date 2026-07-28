import { defineConfig } from "cypress";
import cypressSplit from "cypress-split";

export default defineConfig({
	projectId: "vandxn",
	adminPassword: "admin",
	testUser: "frappe@example.com",
	defaultCommandTimeout: 20000,
	pageLoadTimeout: 15000,
	video: true,
	videoUploadOnPasses: false,
	retries: {
		runMode: 2,
		openMode: 0,
	},
	e2e: {
		baseUrl: "http://pertest:8000",
		setupNodeEvents(on, config) {
			// Splitting tests only works when Cypress Cloud is not orchestrating parallel runs.
			if (process.env.CYPRESS_CLOUD_PARALLEL !== "1") {
				cypressSplit(on, config);
			}

			// The video player soak test measures JS heap growth to catch leaks.
			// Without these flags performance.memory is bucketed to ~5 MB (too
			// coarse to see a slow leak) and there is no way to force a
			// collection, so "growth" would just be uncollected garbage.
			// Chromium-only and otherwise inert, so every other spec is
			// unaffected.
			on("before:browser:launch", (browser = {}, launchOptions) => {
				if (browser.family === "chromium" && browser.name !== "electron") {
					launchOptions.args.push("--enable-precise-memory-info");
					launchOptions.args.push("--js-flags=--expose-gc");
				}
				return launchOptions;
			});

			return config;
		},
	},
});
