import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Pipeline from './views/Pipeline.vue'
import Overview from './views/Overview.vue'
import Growth from './views/Growth.vue'
import Agent from './views/Agent.vue'
import './style.css'

const router = createRouter({ history:createWebHistory(),routes:[
  {path:'/',redirect:'/engineering'}, {path:'/engineering',component:Pipeline},
  {path:'/overview',component:Overview}, {path:'/growth',component:Growth},
  {path:'/agent',component:Agent},
  {path:'/:pathMatch(.*)*',redirect:'/engineering'},
] })
createApp(App).use(router).mount('#app')
