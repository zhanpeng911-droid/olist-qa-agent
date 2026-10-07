<template>
  <div class="shell">
    <aside>
      <div class="brand"><span class="brand-icon">O</span><div><strong>Olist</strong><small>数据工程与经营报表</small></div></div>
      <p class="nav-label">WORKSPACE</p>
      <nav>
        <RouterLink to="/engineering"><span>01</span> 数据工程</RouterLink>
        <RouterLink to="/agent"><span>AI</span> 数据助手</RouterLink>
        <RouterLink to="/overview"><span>A</span> 经营总览</RouterLink>
        <RouterLink to="/growth"><span>B</span> 增长来源</RouterLink>
      </nav>
      <div class="source-card">
        <span :class="['dot',state.meta?.connected?'online':'']"></span>{{!state.meta && state.loading?'正在连接数据库':state.meta?.connected?'数据库已连接':'数据库未连接'}}
        <p>{{state.meta?.database || 'MySQL 8'}}</p><small>数据按购买时间归属<br>指标口径与 Tableau A / B 页一致</small>
      </div>
    </aside>
    <main>
      <header><div><span class="breadcrumb">OLIST / {{route.path==='/agent'?'LLM AGENT':route.path==='/engineering'?'DATA ENGINEERING':'BUSINESS REPORTING'}}</span><h1>{{title}}</h1></div><button class="secondary" :disabled="state.loading" @click="refresh">{{state.loading?'正在刷新…':'刷新数据'}}</button></header>
      <div v-if="state.error" class="error">{{state.error}}</div>
      <div v-if="state.meta && !state.meta.connected" class="warning">无法连接数据库。请启动 MySQL 服务并检查本地 .env 的 DB_* 配置。</div>
      <RouterView :key="route.path"/>
    </main>
  </div>
  <Confirmation/>
</template>
<script setup lang="ts">
import {computed,onMounted} from 'vue'
import {useRoute} from 'vue-router'
import {state,refreshMeta} from './state'
import Confirmation from './components/Confirmation.vue'
const route=useRoute();const title=computed(()=>({'/engineering':'数据工程','/overview':'经营总览','/growth':'增长来源','/agent':'AI 数据助手'}[route.path] || 'Olist'))
async function refresh(){await refreshMeta();window.dispatchEvent(new Event('olist-refresh'))}
onMounted(refreshMeta)
</script>
