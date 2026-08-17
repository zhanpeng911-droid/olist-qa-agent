import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from './layouts/AppLayout.vue'
import DashboardView from './views/DashboardView.vue'
import ChatView from './views/ChatView.vue'

const routes = [
  {
    path: '/',
    component: AppLayout,
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', component: DashboardView, meta: { title: '总览看板', group: 'ANALYTICS' } },
      { path: 'chat', component: ChatView, meta: { title: '智能对话', group: 'ANALYTICS' } },
    ],
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
