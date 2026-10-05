<template>
  <el-container class="app-container">
    <el-header class="app-header">
      <div class="logo">📊 B站视频分析系统</div>

      <el-menu
        mode="horizontal"
        :default-active="$route.path"
        router
        class="nav-menu"
      >
        <el-menu-item index="/">首页</el-menu-item>
        <el-menu-item index="/compare">模型对比</el-menu-item>
      </el-menu>

      <!-- 搜索框: 输入 BV 号 / 链接 -->
      <div class="search-box">
        <el-input
          v-model="searchInput"
          placeholder="输入 BV 号或视频链接, 提交分析"
          clearable
          :disabled="analyzing"
          @keyup.enter="submitAnalyze"
          class="search-input"
        >
          <template #prepend>🔍</template>
        </el-input>
        <el-button
          type="primary"
          :loading="analyzing"
          @click="submitAnalyze"
          class="search-btn"
        >
          {{ analyzing ? '分析中...' : '分析' }}
        </el-button>
      </div>

      <el-button
        type="warning"
        plain
        size="small"
        :loading="reranking"
        @click="doRerank"
        class="rerank-btn"
        title="把数据库里所有视频按最新评分重算排名"
      >
        重算排名
      </el-button>
    </el-header>

    <!-- 分析进度遮罩 -->
    <el-dialog
      v-model="analyzing"
      :show-close="false"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      width="400px"
      align-center
    >
      <div class="analyzing-box">
        <el-icon class="spinner" :size="40"><Loading /></el-icon>
        <h3>正在分析视频</h3>
        <p class="bvid-text">{{ analyzingBvid }}</p>
        <p class="hint">{{ progressHint }}</p>
        <el-progress :percentage="progress" :show-text="false" />
        <p class="elapsed">已用 {{ elapsed }}s</p>
      </div>
    </el-dialog>

    <el-main class="app-main">
      <router-view />
    </el-main>

    <el-footer class="app-footer">
      <span>毕业设计 · 2026</span>
    </el-footer>
  </el-container>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { analyzeNewVideo, rerankAll } from './api'

const router = useRouter()

const searchInput  = ref('')
const analyzing    = ref(false)
const analyzingBvid = ref('')
const elapsed      = ref(0)
const progress     = ref(0)
const progressHint = ref('')
const reranking    = ref(false)

let timer = null
let progressTimer = null

const PROGRESS_STAGES = [
  { at: 2,  pct: 10, hint: '抓取视频元数据...' },
  { at: 6,  pct: 30, hint: '抓取评论中 (300 条)...' },
  { at: 18, pct: 60, hint: 'RoBERTa 情感分析中...' },
  { at: 26, pct: 80, hint: '计算质量系数 Q...' },
  { at: 30, pct: 90, hint: '写入数据库...' },
]

function startProgress() {
  elapsed.value = 0
  progress.value = 0
  progressHint.value = '准备中...'

  timer = setInterval(() => { elapsed.value += 1 }, 1000)

  progressTimer = setInterval(() => {
    const e = elapsed.value
    for (const s of PROGRESS_STAGES) {
      if (e >= s.at) {
        progress.value = s.pct
        progressHint.value = s.hint
      }
    }
    // 持续上升到 95% 防止卡死视觉
    if (progress.value < 95 && elapsed.value > 30) {
      progress.value = Math.min(95, progress.value + 0.5)
    }
  }, 500)
}

function stopProgress() {
  if (timer) clearInterval(timer)
  if (progressTimer) clearInterval(progressTimer)
  timer = null
  progressTimer = null
  progress.value = 100
}

async function submitAnalyze() {
  const input = searchInput.value.trim()
  if (!input) {
    ElMessage.warning('请输入 BV 号或视频链接')
    return
  }

  // 提取 bvid 显示
  const m = input.match(/(BV[a-zA-Z0-9]{10})/)
  analyzingBvid.value = m ? m[1] : input

  analyzing.value = true
  startProgress()

  try {
    const result = await analyzeNewVideo(input)
    stopProgress()

    if (result.__extras?.from_cache) {
      ElMessage.success('视频已存在, 直接展示')
    } else {
      ElMessage.success(`分析完成, 耗时 ${result.__extras?.elapsed_seconds}s`)
    }

    // 跳转到详情页
    setTimeout(() => {
      analyzing.value = false
      searchInput.value = ''
      router.push(`/video/${result.bvid}`)
    }, 600)
  } catch (e) {
    stopProgress()
    analyzing.value = false
    // ElMessage 已经由 axios 拦截器弹出
  }
}

async function doRerank() {
  try {
    await ElMessageBox.confirm(
      '将根据当前所有视频的评分, 重新计算 rank_static / rank_dynamic / rank_change. 此操作通常在累积新视频后执行.',
      '确认重算所有排名',
      {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch { return }   // 取消

  reranking.value = true
  try {
    const result = await rerankAll()
    ElMessage.success(`重算完成, 更新了 ${result.updated_rows} 条记录`)
    // 触发首页刷新
    setTimeout(() => location.reload(), 800)
  } catch (e) {
    // 错误已弹
  } finally {
    reranking.value = false
  }
}

onUnmounted(() => stopProgress())
</script>

<style>
* { box-sizing: border-box; }
body, html { margin: 0; padding: 0; }
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.app-container { min-height: 100vh; }

.app-header {
  background: linear-gradient(90deg, #00aeec 0%, #fb7299 100%);
  color: white;
  display: flex;
  align-items: center;
  height: 64px !important;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  gap: 16px;
}

.logo {
  font-size: 18px;
  font-weight: bold;
  white-space: nowrap;
}

.nav-menu {
  background: transparent !important;
  border-bottom: none !important;
  flex-shrink: 0;
}
.nav-menu .el-menu-item {
  color: white !important;
  border-bottom: none !important;
}
.nav-menu .el-menu-item:hover,
.nav-menu .el-menu-item.is-active {
  background: rgba(255,255,255,0.15) !important;
  color: white !important;
}

.search-box {
  flex: 1;
  display: flex;
  align-items: center;
  max-width: 600px;
  margin: 0 16px;
}
.search-input { flex: 1; }
.search-btn { margin-left: 8px; }

.rerank-btn { margin-left: auto; }

/* 分析中遮罩 */
.analyzing-box {
  text-align: center;
  padding: 16px 8px;
}
.analyzing-box .spinner {
  color: #fb7299;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
.analyzing-box h3 {
  margin: 16px 0 8px;
  color: #303133;
}
.analyzing-box .bvid-text {
  color: #00aeec;
  font-family: Consolas, monospace;
  font-size: 14px;
  margin: 0 0 12px;
}
.analyzing-box .hint {
  color: #606266;
  font-size: 13px;
  margin: 8px 0 12px;
}
.analyzing-box .elapsed {
  color: #909399;
  font-size: 12px;
  margin-top: 8px;
}

.app-main {
  padding: 20px;
  background: #f5f7fa;
  min-height: calc(100vh - 124px);
}

.app-footer {
  text-align: center;
  background: #303133;
  color: #aaa;
  height: 60px !important;
  line-height: 60px;
  font-size: 13px;
}
</style>
