import { call } from 'frappe-ui'

/**
 * Client for the language-platform API surface
 * (lms.lms.language_platform.api.*).
 *
 * Those endpoints return the Technical Agreement §7.3 envelope
 * `{ ok, data, error, meta: { correlationId } }` — this helper unwraps it
 * and throws a normal Error (with `code` and `correlationId` attached)
 * so pages can use try/catch + toasts.
 */

const BASE = 'lms.lms.language_platform.api.'

async function invoke(method, params = {}) {
	const envelope = await call(BASE + method, params)
	if (envelope?.ok) return envelope.data

	const error = new Error(
		envelope?.error?.message || __('Something went wrong. Please try again.')
	)
	error.code = envelope?.error?.code
	error.correlationId = envelope?.meta?.correlationId
	throw error
}

export const startPlacement = (blueprint) => invoke('start_placement', { blueprint })

export const savePlacementAnswer = (attempt, question, answer) =>
	invoke('save_placement_answer', { attempt, question, answer })

export const submitPlacement = (attempt, answers) =>
	invoke('submit_placement', { attempt, answers: JSON.stringify(answers) })

export const getPlacementResult = (attempt) => invoke('get_placement_result', { attempt })

export const getLessonOverlays = (lesson, batch = null) =>
	invoke('get_lesson_overlays', { lesson, batch })

export const saveOverlay = (overlayData, expectedVersion = null) =>
	invoke('save_overlay', {
		overlay_data: JSON.stringify(overlayData),
		expected_version: expectedVersion,
	})

export const submitOverlayAnswer = (overlayName, answer) =>
	invoke('submit_overlay_answer', { overlay_name: overlayName, answer })

export const createSpeakingSubmission = (prompt, fileUrl, durationSeconds) =>
	invoke('create_speaking_submission', {
		prompt,
		audio_file: fileUrl,
		duration_seconds: durationSeconds,
	})

export const getSpeakingResult = (submission) => invoke('get_speaking_result', { submission })

export const getGradingQueue = () => invoke('get_grading_queue')

export const overrideSpeakingScore = (submission, finalScore, reason) =>
	invoke('override_speaking_score', {
		submission,
		final_score: finalScore,
		reason,
	})

/**
 * Upload a recorded Blob (e.g. MediaRecorder output) as a private file
 * attached to the given document, using Frappe's upload endpoint.
 */
export async function uploadBlob(blob, filename, { doctype, docname } = {}) {
	const formData = new FormData()
	formData.append('file', blob, filename)
	formData.append('is_private', '1')
	if (doctype) formData.append('doctype', doctype)
	if (docname) formData.append('docname', docname)

	const response = await fetch('/api/method/upload_file', {
		method: 'POST',
		headers: {
			'X-Frappe-CSRF-Token': window.csrf_token,
			Accept: 'application/json',
		},
		credentials: 'include',
		body: formData,
	})
	if (!response.ok) {
		throw new Error(__('Audio upload failed. Please try again.'))
	}
	const data = await response.json()
	return data.message // File doc, has file_url
}
