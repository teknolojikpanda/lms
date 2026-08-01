/**
 * When a timestamped overlay becomes due.
 *
 * Kept out of the component, and free of Vue, so the rule can be tested
 * directly — it decides whether a student is shown a question or skips it
 * unnoticed, which is not something to verify by mounting a video player.
 */

/** How far past a timestamp still counts as "landed on it". */
export const LANDING_WINDOW_SECONDS = 1.5

/**
 * The overlay that playback has just become due for, or `null`.
 *
 * `previousTime` is the last position this was called with, or `null` on
 * a fresh player. An overlay is due once playback *crosses* its
 * timestamp: a position landing inside a narrow window after it is not
 * required, and requiring one was the bug. `timeupdate` fires every
 * 200-250ms at best and far more coarsely under load or at speed, and a
 * seek moves the position arbitrarily far in a single step — so jumping
 * from before a question to more than a second past it skipped it
 * permanently, and a student could seek over every question in a lesson.
 *
 * Returns the earliest crossed overlay so that one jump over several
 * shows them in order; the caller advances its clock to the returned
 * overlay rather than to the current position, leaving the rest still
 * ahead of `previousTime`.
 */
export function findDueOverlay(overlays, previousTime, currentTime, isShown) {
	const due = (overlays || []).filter((overlay) => {
		if (isShown(overlay)) return false

		const at = overlay.timestamp_ms / 1000
		if (currentTime < at) return false

		// No previous position means a fresh load, or a lesson resumed
		// partway through. Only something we have just landed on is due —
		// everything earlier was passed before this player existed, and
		// firing all of it at once would be its own bug.
		if (previousTime === null || previousTime === undefined) {
			return currentTime - at <= LANDING_WINDOW_SECONDS
		}

		return previousTime < at || currentTime - at <= LANDING_WINDOW_SECONDS
	})

	if (!due.length) return null
	return due.reduce((earliest, overlay) =>
		overlay.timestamp_ms < earliest.timestamp_ms ? overlay : earliest
	)
}
