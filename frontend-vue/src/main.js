import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './MainApp.vue'
import './styles/main.css'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
