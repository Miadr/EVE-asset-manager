import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css' // 引入暗黑模式样式
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import Dashboard from './components/Dashboard.vue'
import axios from 'axios'

// --- 配置网络请求工具 (Axios) ---
// 由于已移除了前端的权限校验，这里直接去掉了 token 和 401 拦截相关的代码

// --- 2. 配置页面路由 ---
const router = createRouter({
    history: createWebHistory(),
    routes: [
        { path: '/', redirect: '/dashboard' },
        { path: '/dashboard', component: Dashboard }
    ]
})

// --- 3. 启动应用 ---
const app = createApp(App)
// 注册所有图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
    app.component(key, component)
}
app.use(router)
app.use(ElementPlus)
app.mount('#app')