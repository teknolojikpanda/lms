import { describe, expect, it, vi, beforeEach } from 'vitest'

// Every block mounts a Vue app into the block's wrapper, but only the PDF
// branch used to keep a handle on it. destroy() therefore unmounted
// nothing for video, audio or the uploader, so onBeforeUnmount never ran —
// and VideoBlock clears its watermark rotation interval in that hook. Each
// watermarked video visited or re-rendered left a live timer and a
// reactive component behind for the life of the page.
const mocks = vi.hoisted(() => ({
	unmounted: [] as string[],
}))

function stubBlock(label: string) {
	return {
		default: {
			name: label,
			template: `<div data-block="${label}" />`,
			beforeUnmount() {
				mocks.unmounted.push(label)
			},
		},
	}
}

vi.mock('@/components/VideoBlock.vue', () => stubBlock('video'))
vi.mock('@/components/AudioBlock.vue', () => stubBlock('audio'))
vi.mock('@/components/PdfBlock.vue', () => stubBlock('pdf'))
vi.mock('@/components/UploadPlugin.vue', () => stubBlock('uploader'))

vi.mock('@/utils/dialogs', () => ({ createDialog: vi.fn() }))
vi.mock('@/translation', () => ({ default: { install: () => {} } }))

import { Upload } from '@/utils/upload'

function makeBlock(data = {}) {
	const block = new Upload({ data, config: {}, readOnly: false })
	block.render()
	return block
}

describe('Upload block teardown', () => {
	beforeEach(() => {
		mocks.unmounted.length = 0
	})

	it.each([
		['video', { file_url: '/files/a.mp4', file_type: 'mp4' }],
		['audio', { file_url: '/files/a.mp3', file_type: 'mp3' }],
		['pdf', { file_url: '/files/a.pdf', file_type: 'PDF' }],
	])('destroy() unmounts a %s block', (label, data) => {
		const block = makeBlock(data)

		block.destroy()

		expect(mocks.unmounted).toContain(label)
	})

	it('destroy() unmounts the uploader when no file was chosen', () => {
		const block = makeBlock({})

		block.destroy()

		expect(mocks.unmounted).toContain('uploader')
	})

	it('replacing a rendered block unmounts the previous one', () => {
		const block = makeBlock({ file_url: '/files/a.mp4', file_type: 'mp4' })

		block.renderFile({ file_url: '/files/b.mp3', file_type: 'mp3' })

		expect(mocks.unmounted).toContain('video')
	})

	it('rendering an image unmounts what it replaces', () => {
		// The image branch overwrites the wrapper's markup, so anything
		// mounted there would keep running with its DOM torn out.
		const block = makeBlock({ file_url: '/files/a.mp4', file_type: 'mp4' })

		block.renderFile({ file_url: '/files/a.png', file_type: 'png' })

		expect(mocks.unmounted).toContain('video')
	})

	it('does not remount after destroy()', async () => {
		// The uploader hands back through a microtask so it is not unmounted
		// mid-handler — but EditorJS may have removed the block by the time
		// that runs, and mounting then revives a block the editor has torn
		// down, into a wrapper no longer in the document.
		const block = makeBlock({})

		block.destroy()
		block.renderFile({ file_url: '/files/a.mp4', file_type: 'mp4' })
		await Promise.resolve()

		expect(block.app).toBeNull()
		expect(block.wrapper.querySelector('[data-block="video"]')).toBeNull()
	})

	it('destroy() is safe to call twice', () => {
		const block = makeBlock({ file_url: '/files/a.mp4', file_type: 'mp4' })

		block.destroy()
		expect(() => block.destroy()).not.toThrow()
		expect(mocks.unmounted.filter((entry) => entry === 'video')).toHaveLength(1)
	})
})
