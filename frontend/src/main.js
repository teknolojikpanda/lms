import './index.css'
import { createApp, watch } from 'vue'
import router from './router'
import App from './App.vue'
import { createPinia } from 'pinia'
import dayjs from '@/utils/dayjs'
import { createDialog } from '@/utils/dialogs'
import translationPlugin from './translation'
import { usersStore } from './stores/user'
import { initSocket } from './socket'
import { FrappeUI, setConfig, frappeRequest, pageMetaPlugin } from 'frappe-ui'
import { telemetryPlugin } from 'frappe-ui/frappe'
import { initAccessibility, loadAccessibility } from './stores/accessibility'

// Before mount: applies the cached font step and contrast mode so the very
// first paint is already correct. A user who needs 24px text should never
// read 16px text while an API call resolves (§6.3).
initAccessibility()

let pinia = createPinia()
let app = createApp(App)
setConfig('resourceFetcher', frappeRequest)

app.use(FrappeUI)
app.use(pinia)
app.use(router)
app.use(translationPlugin)
app.use(pageMetaPlugin)
app.provide('$dayjs', dayjs)
app.provide('$socket', initSocket())
app.mount('#app')

const { userResource, allUsers } = usersStore()
app.provide('$user', userResource)
app.provide('$allUsers', allUsers)

watch(userResource, () => {
	if (userResource.data) {
		app.use(telemetryPlugin, { app_name: 'lms' })
		// Reconcile with the server copy so settings follow the user
		// between devices.
		loadAccessibility()
	}
})

app.config.globalProperties.$user = userResource
app.config.globalProperties.$dialog = createDialog
