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
          <component :is="item.icon" :size="17" :stroke-width="1.8" class="nav-ico" />
          <span class="nav-label">{{ item.title }}</span>
          <span v-if="item.tag" class="nav-tag">{{ item.tag }}</span>
        </router-link>
      </div>

      <!-- 会话管理 -->
      <div class="session-group">
        <div class="nav-group-label">CONVERSATIONS</div>
        <button class="new-session" @click="startNew">
          <Plus :size="15" :stroke-width="2" />
          <span>新建对话</span>
        </button>
        <div class="session-list" v-if="sessions.length">
          <div
            v-for="s in sessions"
            :key="s.id"
            class="session-item"
            :class="{ active: s.id === currentId && route.path === '/chat' }"
            @click="openSession(s.id)"
          >
            <MessageSquare :size="14" :stroke-width="1.8" class="si-ico" />
            <span class="si-title" :title="s.title">{{ s.title }}</span>
            <button class="si-del" title="删除会话" @click.stop="remove(s.id)">
              <Trash2 :size="13" />
            </button>
          </div>
        </div>
      </div>

      <div class="sidebar-footer">
        <div class="footer-head"><span class="dot"></span><span class="footer-status">数据源已连接</span></div>
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
          <el-input v-model="searchQ" placeholder="搜索或输入你的问题…" clearable @keyup.enter="doSearch">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
        <div class="topbar-right">
          <span v-if="route.path === '/dashboard'" class="range-tag">
            <Calendar :size="13" /> 全量数据 · 2016-09 ~ 2018-10 <span class="chev">⌄</span>
          </span>
          <button class="icon-btn" aria-label="通知"><el-icon :size="18"><Bell /></el-icon></button>
          <div class="user">
            <div class="user-avatar">企</div>
            <div class="user-text">
              <div class="user-name">企业分析员 <span class="role-badge">业务分析</span></div>
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
import {
  Bell, Calendar, LayoutDashboard, MessageSquare, MessageSquareCode, Plus, Search, Trash2,
} from 'lucide-vue-next'
import { getMeta } from '../api'
import { useSessions } from '../composables/useSessions'

const route = useRoute()
const router = useRouter()
const meta = ref<any>(null)
const searchQ = ref('')
const { sessions, currentId, newSession, switchSession, deleteSession } = useSessions()

const sourceLabel = computed(() => meta.value?.source_label ?? '加载中…')

const groups = [
  {
    name: 'ANALYTICS',
    items: [
      { path: '/dashboard', title: '总览看板', icon: LayoutDashboard },
      { path: '/chat', title: '智能对话', icon: MessageSquareCode, tag: 'AI' },
    ],
  },
]

function startNew() {
  const id = newSession()
  router.push({ path: '/chat', query: { session: id } })
}
function openSession(id: string) {
  switchSession(id)
  router.push({ path: '/chat', query: { session: id } })
}
function remove(id: string) {
  deleteSession(id)
  if (route.path === '/chat') router.replace({ path: '/chat', query: { session: currentId.value } })
}

function doSearch() {
  const q = searchQ.value.trim()
  if (!q) return
  router.push({ path: '/chat', query: { q } })
  searchQ.value = ''
}

onMounted(async () => {
  try { meta.value = await getMeta() } catch { /* 后端未启动 */ }
})
</script>

<style scoped>
.layout { display: flex; height: 100vh; }

/* 侧边栏 */
.sidebar {
  width: 248px;
  background: var(--card);
  border-right: 1px solid var(--border-soft);
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}
.brand { display: flex; align-items: center; gap: 12px; padding: 0 8px 26px; }
.brand-logo {
  width: 40px; height: 40px; border-radius: 12px;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  color: #fff; font-weight: 700; font-size: 20px;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 8px 18px -6px rgba(47,101,246,.4);
}
.brand-name { font-weight: 700; font-size: 16px; }
.brand-sub { font-size: 12px; color: var(--text-3); }

.nav-group, .session-group { margin-bottom: 20px; }
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
.nav-item.active { background: #EFF6FF; color: #2563EB; font-weight: 600; }
.nav-item.active::before {
  content: ''; position: absolute; left: -16px; top: 8px; bottom: 8px;
  width: 3px; border-radius: 3px; background: #2F65F6;
}
.nav-ico { flex-shrink: 0; }
.nav-label { flex: 1; }
.nav-tag {
  font-size: 10px; font-weight: 700; color: #2563EB;
  background: #EFF6FF; padding: 1px 8px; border-radius: var(--radius-pill);
}

/* 会话管理 */
.new-session {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  width: 100%; padding: 8px 12px; margin-bottom: 8px;
  background: #EFF6FF; color: #2563EB;
  border: none; border-radius: var(--radius-pill);
  font-size: 13px; font-weight: 600; cursor: pointer;
  transition: all .18s ease;
}
.new-session:hover { background: #DBE5FE; }
.session-list { display: flex; flex-direction: column; gap: 2px; max-height: 240px; overflow-y: auto; }
.session-item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-radius: var(--radius-sm);
  cursor: pointer; color: var(--text-2); font-size: 13px;
  transition: background .15s ease;
}
.session-item:hover { background: rgba(47,101,246,.05); }
.session-item.active { background: #EFF6FF; color: #2563EB; }
.si-ico { flex-shrink: 0; }
.si-title {
  flex: 1; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
.si-del {
  border: none; background: transparent; color: var(--text-3); cursor: pointer;
  display: none; padding: 3px; border-radius: 6px; flex-shrink: 0;
}
.session-item:hover .si-del { display: inline-flex; }
.si-del:hover { color: var(--red); background: var(--red-bg); }

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
.topbar-search :deep(.el-input__wrapper) { border-radius: var(--radius-pill); background: var(--bg); box-shadow: none !important; }
.topbar-right { display: flex; align-items: center; gap: 18px; min-width: 0; flex-shrink: 0; }
.range-tag {
  font-size: 12px; color: var(--text-2); background: var(--bg);
  padding: 6px 12px; border-radius: var(--radius-pill);
  white-space: nowrap; display: inline-flex; align-items: center; gap: 5px;
}
.range-tag .chev { color: var(--text-3); font-size: 10px; }
.icon-btn { border: none; background: var(--bg); cursor: pointer; width: 38px; height: 38px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--text-2); flex-shrink: 0; }
.icon-btn:hover { background: #EFF6FF; color: var(--primary); }
.user { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.user-avatar {
  width: 38px; height: 38px; border-radius: 50%;
  background: linear-gradient(135deg, #E0E7FF, #DBEAFE);
  color: #4338CA; font-weight: 700; font-size: 15px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.user-text { white-space: nowrap; text-align: left; }
.user-name { font-size: 14px; font-weight: 600; line-height: 1.2; display: flex; align-items: center; gap: 6px; }
.role-badge {
  font-size: 10px; font-weight: 600; color: #4338CA;
  background: #EEF2FF; padding: 1px 8px; border-radius: var(--radius-pill);
}
.user-hello { font-size: 11px; color: var(--text-3); line-height: 1.3; }

.content { flex: 1; overflow-y: auto; padding: 28px 32px; }
</style>
