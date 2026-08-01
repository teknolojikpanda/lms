import AudioBlock from '@/components/AudioBlock.vue'
import VideoBlock from '@/components/VideoBlock.vue'
import PdfBlock from '@/components/PdfBlock.vue'
import UploadPlugin from '@/components/UploadPlugin.vue'
import { h, createApp } from 'vue'
import { Upload as UploadIcon } from 'lucide-vue-next'
import { createDialog } from '@/utils/dialogs'
import translationPlugin from '../translation'

export class Upload {
	constructor({ data, api, config, readOnly }) {
		this.data = data
		this.readOnly = readOnly
		this.config = config || {}
	}

	static get toolbox() {
		const app = createApp({
			render: () =>
				h(UploadIcon, { size: 18, strokeWidth: 1.5, color: 'black' }),
		})

		const div = document.createElement('div')
		app.mount(div)

		return {
			title: 'Upload',
			icon: div.innerHTML,
		}
	}

	static get isReadOnlySupported() {
		return true
	}

	render() {
		this.wrapper = document.createElement('div')

		if (this.data && this.data.file_url) {
			this.renderFile(this.data)
		} else {
			this.renderFileUploader()
		}

		return this.wrapper
	}

	/**
	 * Mount a block component, replacing whatever this block had before.
	 *
	 * Every branch goes through here so `this.app` always holds the app
	 * actually mounted. Only the PDF branch used to track it, so
	 * `destroy()` unmounted nothing for the others — and `onBeforeUnmount`
	 * never ran. A watermarked VideoBlock rotates its mark on an interval
	 * cleared in that hook, so every such video visited or re-rendered
	 * left a live timer and a reactive component behind for the life of
	 * the page. The uploader leaked the same way each time a file replaced
	 * it.
	 */
	mountApp(component, props, { translate = false } = {}) {
		this.unmountApp()
		const app = createApp(component, props)
		if (translate) app.use(translationPlugin)
		app.config.globalProperties.$dialog = createDialog
		app.mount(this.wrapper)
		this.app = app
		return app
	}

	unmountApp() {
		if (!this.app) return
		this.app.unmount()
		this.app = null
	}

	renderFile(file) {
		if (this.isVideo(file.file_type)) {
			this.mountApp(
				VideoBlock,
				{
					file: file.file_url,
					readOnly: this.readOnly,
					quizzes: file.quizzes || [],
					saveQuizzes: (quizzes) => {
						if (this.readOnly) return
						this.data.quizzes = quizzes
					},
				},
				{ translate: true }
			)
			return
		} else if (this.isAudio(file.file_type)) {
			this.mountApp(AudioBlock, { file: file.file_url })
			return
		} else if (file.file_type == 'PDF') {
			// iOS Safari (all WebKit browsers) refuses to scroll a PDF in an
			// <iframe>, so render it inline via pdf.js. mount()/unmount() is tracked
			// so destroy() can tear the pdf.js worker + render tasks down.
			this.mountApp(PdfBlock, { file: file.file_url }, { translate: true })
			return
		} else {
			// An image replaces the block's markup outright, so anything
			// mounted here has to come down first or it keeps running with
			// its DOM torn out from under it.
			this.unmountApp()
			this.wrapper.innerHTML = `<img class="mb-4" src=${encodeURI(
				file.file_url
			)} width='100%'>`
			return
		}
	}

	renderFileUploader() {
		this.mountApp(
			UploadPlugin,
			{
				uploadContext: this.config,
				onFileUploaded: (file) => {
					this.data.file_url = file.file_url
					this.data.file_type = file.file_type
					// The uploader is unmounted as part of rendering the file,
					// so hand back to the caller first: unmounting an app from
					// inside its own event handler tears down the component
					// that is still running.
					queueMicrotask(() => this.renderFile(file))
				},
			},
			{ translate: true }
		)
	}

	validate(savedData) {
		if (!savedData.file_url || !savedData.file_type) {
			return false
		}
		return true
	}

	save(blockContent) {
		return {
			file_url: this.data.file_url,
			file_type: this.data.file_type,
			quizzes: this.data.quizzes || [],
		}
	}

	// EditorJS calls destroy() when a block is removed or the editor is torn down.
	// Unmounting fires the block's onBeforeUnmount: PdfBlock cancels render tasks,
	// destroys the document and releases the shared pdf.js worker; VideoBlock
	// clears its watermark rotation interval.
	destroy() {
		this.unmountApp()
	}

	isVideo(type) {
		return ['mov', 'mp4', 'avi', 'mkv', 'webm'].includes(type.toLowerCase())
	}

	isAudio(type) {
		return ['mp3', 'wav', 'ogg'].includes(type.toLowerCase())
	}
}
