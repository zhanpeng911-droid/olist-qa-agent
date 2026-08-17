<template>
  <div class="layout">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-logo">O</div>
        <div class="brand-text">
          <div class="brand-name">Olist</div>
          <div class="brand-sub">智能问数 Agent</div>
        </div>
      </div>

      <div class="nav-group" v-for="group in groups" :key="group.name">
        <div class="nav-group-label">{{ group.name }}</div>
        <router-link
          v-for="item in group.items" :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: route.path === item.path }"
        >
          <span class="nav-icon" v-html="item.icon"></span>
          <span>{{ item.title }}</span>
        </router-link>
      </div>

      <div class="sidebar-footer">
        <div class="footer-head">
          <span class="dot"></span>
          <span class="footer-status">数据源已连接</span>
        </div>
        <div class="footer-src">{{ sourceLabel }}</div>
        <div class="footer-note">只读分析 · 口径锁死 · 可对账</div>
      </div>
    </aside>

    <!-- 主区 -->
    <div class="main">
      <header class="topbar">
        <div class="topbar-left">
          <h1>{{ route.meta.title || '总览' }}</h1>
        </div>
        <div class="topbar-search">
          <el-input
            v-model="searchQ"
            placeholder="搜索或输入你的问题…"
            clearable
            @keyup.enter="doSearch"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
        <div class="topbar-right">
          <span v-if="route.path === '/dashboard'" class="range-tag">
            📅 全量数据 · 2016-09 ~ 2018-10 <span class="chev">⌄</span>
          </span>
          <button class="icon-btn" aria-label="通知"><el-icon :size="18"><Bell /></el-icon></button>
          <div class="user">
            <div class="avatar">企</div>
            <div class="user-text">
              <div class="user-name">企业分析员</div>
              <div class="user-hello">Welcome Back 👋</div>
            </div>
          </div>
        </div>
      </header>

      <main class="content">
        <router-view :key="route.path" />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bell, Search } from '@element-plus/icons-vue'
import { getMeta } from '../api'

const route = useRoute()
const router = useRouter()
const meta = ref<any>(null)
const searchQ = ref('')

const sourceLabel = computed(() => meta.value?.source_label ?? '加载中…')

// 顶部搜索：跳转到对话页并自动发送问题
function doSearch() {
  const q = searchQ.value.trim()
  if (!q) return
  router.push({ path: '/chat', query: { q } })
  searchQ.value = ''
}

const groups = [
  {
    name: 'ANALYTICS',
    items: [
      { path: '/dashboard', title: '总览看板', icon: '📊' },
      { path: '/chat', title: '智能对话', icon: '💬' },
    ],
  },
]

onMounted(async () => {
  try { meta.value = await getMeta() } catch { /* 后端未启动 */ }
})
</script>

<style scoped>
.layout { display: flex; height: 100vh; }

/* 侧边栏 */
.sidebar {
  width: 236px;
  background: var(--card);
  border-right: 1px solid var(--border-soft);
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
}
.brand { display: flex; align-items: center; gap: 12px; padding: 0 8px 28px; }
.brand-logo {
  width: 40px; height: 40px; border-radius: 12px;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  color: #fff; font-weight: 700; font-size: 20px;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 8px 18px -6px rgba(47,101,246,.4);
}
.brand-name { font-weight: 700; font-size: 16px; }
.brand-sub { font-size: 12px; color: var(--text-3); }

.nav-group { margin-bottom: 22px; }
.nav-group-label {
  font-size: 11px; font-weight: 600; color: var(--text-3);
  letter-spacing: 1.2px; padding: 0 12px 8px;
}
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; margin-bottom: 4px;
  border-radius: var(--radius-md);
  color: var(--text-2); text-decoration: none;
  font-size: 14px; font-weight: 500;
  position: relative; transition: all .18s ease;
  border: none; outline: none;
}
.nav-item:hover { background: rgba(47,101,246,.05); color: var(--text-1); }
/* 统一选中态：浅蓝背景 + 蓝字 + 左侧 3px 高亮条 */
.nav-item.active {
  background: #EFF6FF;
  color: #2563EB; font-weight: 600;
}
.nav-item.active::before {
  content: ''; position: absolute; left: -16px; top: 8px; bottom: 8px;
  width: 3px; border-radius: 3px; background: #2F65F6;
}
.nav-icon { font-size: 16px; }

.sidebar-footer {
  margin-top: auto; padding: 14px;
  background: var(--bg); border-radius: var(--radius-md);
  border: 1px solid var(--border-soft);
}
.footer-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 3px rgba(16,185,129,.15); }
.footer-status { font-size: 12px; color: var(--text-2); font-weight: 600; }
.footer-src { font-size: 13px; color: var(--text-1); font-weight: 700; margin-bottom: 4px; word-break: break-all; }
.footer-note { font-size: 11px; color: var(--text-3); }

/* 主区 */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar {
  height: 72px; padding: 0 28px 0 32px;
  display: flex; align-items: center; justify-content: space-between;
  gap: 20px;
  background: var(--card); border-bottom: 1px solid var(--border-soft);
  flex-shrink: 0;
}
.topbar h1 { font-size: 20px; font-weight: 700; margin: 0; white-space: nowrap; }
.topbar-search { flex: 1; max-width: 420px; }
.topbar-search :deep(.el-input__wrapper) {
  border-radius: var(--radius-pill);
  background: var(--bg);
  box-shadow: none !important;
}
.topbar-right {
  display: flex; align-items: center; gap: 18px;
  min-width: 0; flex-shrink: 0;
}
.period { width: 120px; flex-shrink: 0; }
.range-tag {
  font-size: 12px; color: var(--text-2); background: var(--bg);
  padding: 6px 12px; border-radius: var(--radius-pill);
  white-space: nowrap; display: inline-flex; align-items: center; gap: 4px;
}
.range-tag .chev { color: var(--text-3); font-size: 10px; }
.icon-btn {
  border: none; background: var(--bg); cursor: pointer;
  width: 38px; height: 38px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: var(--text-2); flex-shrink: 0;
}
.icon-btn:hover { background: #EFF6FF; color: var(--primary); }
.user { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.avatar {
  width: 38px; height: 38px; border-radius: 50%;
  background: linear-gradient(135deg, var(--sky), var(--primary));
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-weight: 600; flex-shrink: 0;
}
.user-text { white-space: nowrap; text-align: left; }
.user-name { font-size: 14px; font-weight: 600; line-height: 1.2; }
.user-hello { font-size: 11px; color: var(--text-3); line-height: 1.3; }

.content { flex: 1; overflow-y: auto; padding: 28px 32px; }
</style>
