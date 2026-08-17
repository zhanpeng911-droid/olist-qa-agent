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
        <span class="dot"></span> 数据源：{{ meta?.source_label || '…' }}
      </div>
    </aside>

    <!-- 主区 -->
    <div class="main">
      <header class="topbar">
        <div class="topbar-left">
          <h1>{{ route.meta.title || '总览' }}</h1>
        </div>
        <div class="topbar-right">
          <el-select v-if="route.path === '/dashboard'" v-model="period" class="period">
            <el-option label="近 30 天" value="month" />
            <el-option label="近 90 天" value="quarter" />
            <el-option label="全年" value="year" />
          </el-select>
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
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Bell } from '@element-plus/icons-vue'
import { getMeta } from '../api'

const route = useRoute()
const period = ref('month')
const meta = ref<any>(null)

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
  margin-top: auto; padding: 12px;
  font-size: 12px; color: var(--text-3);
  background: var(--bg); border-radius: var(--radius-md);
  display: flex; align-items: center; gap: 8px;
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); }

/* 主区 */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar {
  height: 72px; padding: 0 24px 0 32px;
  display: flex; align-items: center; justify-content: space-between;
  background: var(--card); border-bottom: 1px solid var(--border-soft);
  flex-shrink: 0;
}
.topbar h1 { font-size: 20px; font-weight: 700; margin: 0; white-space: nowrap; }
.topbar-right {
  display: flex; align-items: center; gap: 18px;
  min-width: 0;
}
.period { width: 120px; flex-shrink: 0; }
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
