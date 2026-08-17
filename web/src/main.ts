import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import './styles/tokens.css'

const app = createApp(App)

// 全局错误捕获：避免白屏，把错误显示在页面底部便于定位
app.config.errorHandler = (err) => {
  console.error('[UI 渲染错误]', err)
  let el = document.getElementById('ui-error-bar')
  if (!el) {
    el = document.createElement('div')
    el.id = 'ui-error-bar'
    el.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#F43F5E;color:#fff;padding:10px 16px;font:12px/1.5 monospace;white-space:pre-wrap;max-height:40vh;overflow:auto;'
    document.body.appendChild(el)
  }
  const errObj = err as any
  el.textContent = `⚠ UI 渲染错误: ${errObj?.message ?? String(err)}\n${errObj?.stack ?? ''}`
}
window.addEventListener('unhandledrejection', (e) => {
  console.error('[未处理 Promise 拒绝]', e.reason)
})

app.use(createPinia()).use(router).use(ElementPlus).mount('#app')
