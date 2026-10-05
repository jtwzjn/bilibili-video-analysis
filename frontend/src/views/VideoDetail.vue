<template>
  <div v-loading="loading" class="detail-page">
    <!-- 返回按钮 -->
    <el-button @click="$router.back()" class="back-btn">
      ← 返回
    </el-button>

    <template v-if="video">
      <!-- 标题区 -->
      <el-card shadow="never" class="header-card">
        <h2 class="title">{{ video.title }}</h2>
        <div class="meta">
          <el-tag size="small">{{ video.category }}</el-tag>
          <span class="meta-item">UP主: {{ video.owner_name }}</span>
          <span class="meta-item">时长: {{ formatDuration(video.duration) }}</span>
          <span class="meta-item">发布: {{ formatDate(video.pubdate) }}</span>
          <a :href="`https://www.bilibili.com/video/${video.bvid}`" target="_blank" class="ext-link">
            🔗 在 B 站查看
          </a>
        </div>
        <p v-if="video.description" class="desc">{{ truncate(video.description, 200) }}</p>
      </el-card>

      <!-- 基础数据 -->
      <el-row :gutter="16" class="stats-row">
        <el-col :span="4"><el-card class="stat-mini"><div class="num">{{ formatNum(video.view_count) }}</div><div class="lbl">播放</div></el-card></el-col>
        <el-col :span="4"><el-card class="stat-mini"><div class="num">{{ formatNum(video.like_count) }}</div><div class="lbl">点赞</div></el-card></el-col>
        <el-col :span="4"><el-card class="stat-mini"><div class="num">{{ formatNum(video.coin_count) }}</div><div class="lbl">投币</div></el-card></el-col>
        <el-col :span="4"><el-card class="stat-mini"><div class="num">{{ formatNum(video.favorite_count) }}</div><div class="lbl">收藏</div></el-card></el-col>
        <el-col :span="4"><el-card class="stat-mini"><div class="num">{{ formatNum(video.share_count) }}</div><div class="lbl">分享</div></el-card></el-col>
        <el-col :span="4"><el-card class="stat-mini"><div class="num">{{ formatNum(video.reply_count) }}</div><div class="lbl">评论</div></el-card></el-col>
      </el-row>

      <!-- 评分对比 -->
      <el-card shadow="never" class="score-card">
        <template #header><b>评分对比</b></template>
        <el-row :gutter="20">
          <el-col :span="8">
            <div class="score-block static">
              <div class="score-label">静态评分 (基线)</div>
              <div class="score-value">{{ (video.score_static || 0).toFixed(4) }}</div>
              <div class="score-rank">排名 #{{ video.rank_static }}</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="score-block dynamic">
              <div class="score-label">动态评分 (RoBERTa 加权)</div>
              <div class="score-value">{{ (video.score_dynamic || 0).toFixed(4) }}</div>
              <div class="score-rank">排名 #{{ video.rank_dynamic }}</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="score-block change">
              <div class="score-label">排名变化</div>
              <div class="score-value" :class="rankClass(video.rank_change)">
                {{ formatRankChange(video.rank_change) }}
              </div>
              <div class="score-rank">{{ rankInterpretation(video.rank_change) }}</div>
            </div>
          </el-col>
        </el-row>
      </el-card>

      <!-- 雷达图 + 情感饼图 -->
      <el-row :gutter="16" class="chart-row">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><b>质量系数 Q 三维分解</b></template>
            <div ref="radarRef" class="chart-box"></div>
            <div class="chart-note">
              S = 加权情感得分 | C = 一致性 | D = 讨论深度<br>
              <b>Q = {{ (video.quality_q || 0).toFixed(3) }}</b>
              ({{ video.comment_count_used || 0 }} 条评论参与计算)
            </div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><b>评论情感分布</b></template>
            <div ref="pieRef" class="chart-box"></div>
            <div class="chart-note">
              基于 RoBERTa 中文情感模型 (置信度 < 0.6 视为中性)
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 评论列表 -->
      <el-card shadow="never" class="comments-card">
        <template #header>
          <div class="comments-header">
            <b>评论列表</b>
            <div class="comment-filters">
              <el-radio-group v-model="commentFilters.sort" size="small" @change="onFilterChange">
                <el-radio-button label="hot">热度</el-radio-button>
                <el-radio-button label="latest">最新</el-radio-button>
                <el-radio-button label="sentiment_pos">最正向</el-radio-button>
                <el-radio-button label="sentiment_neg">最负向</el-radio-button>
              </el-radio-group>
              <el-radio-group v-model="commentFilters.label" size="small" @change="onFilterChange" style="margin-left: 12px;">
                <el-radio-button label="">全部</el-radio-button>
                <el-radio-button label="positive">正向</el-radio-button>
                <el-radio-button label="negative">负向</el-radio-button>
                <el-radio-button label="neutral">中性</el-radio-button>
              </el-radio-group>
            </div>
          </div>
        </template>
	<div v-loading="commentsLoading" class="comments-list">
  <div v-for="c in comments" :key="c.rpid" class="comment-item">
    <div class="comment-head">
      <span class="uname">{{ c.uname }}</span>
      <el-tag size="small" type="info" effect="plain">Lv{{ c.user_level }}</el-tag>
      <el-tag size="small" :type="sentimentTagType(c.sentiment_label)">
        {{ sentimentLabelCN(c.sentiment_label) }} {{ (c.sentiment_score || 0).toFixed(2) }}
      </el-tag>
      <span class="like-count">👍 {{ c.like_count }}</span>
      <span class="ctime">{{ formatDate(c.ctime) }}</span>
    </div>
    <div class="comment-msg">{{ c.message }}</div>
  </div>

  <el-empty v-if="!commentsLoading && comments.length === 0" description="暂无评论" />

  <!-- 加载更多 / 状态显示 -->
  <div v-if="comments.length > 0" class="load-more-bar">
    <span class="loaded-info">
      已加载 {{ commentsLoaded }} / {{ commentsTotal }} 条
    </span>
    <el-button
      v-if="commentsLoaded < commentsTotal"
      type="primary"
      plain
      :loading="commentsLoading"
      @click="loadComments(true)"
    >
      加载更多 ({{ Math.min(COMMENTS_PAGE_SIZE, commentsTotal - commentsLoaded) }} 条)
    </el-button>
    <span v-else class="all-loaded">— 已加载全部 —</span>
  </div>
</div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import {
  getVideoDetail, getVideoSentiment, getVideoComments
} from '../api'

const route = useRoute()
const bvid = route.params.bvid

// 状态
const loading = ref(true)
const video = ref(null)
const sentiment = ref(null)
const comments = ref([])
const commentsLoading = ref(false)
const commentsTotal = ref(0)        // 新增: 该视频该筛选条件下的总评论数
const commentsLoaded = ref(0)        // 新增: 已加载多少条
const COMMENTS_PAGE_SIZE = 50        // 新增: 每次加载多少条

const commentFilters = reactive({
  sort: 'hot',
  label: '',
})

// 图表 refs
const radarRef = ref(null)
const pieRef = ref(null)
let radarChart = null
let pieChart = null

// ============ 加载 ============
async function loadAll() {
  loading.value = true
  try {
    const [v, s] = await Promise.all([
      getVideoDetail(bvid),
      getVideoSentiment(bvid),
    ])
    video.value = v
    sentiment.value = s
    await nextTick()
    renderRadar()
    renderPie()
    loadComments()
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function loadComments(append = false) {
  commentsLoading.value = true
  try {
    const offset = append ? commentsLoaded.value : 0
    const params = {
      sort: commentFilters.sort,
      limit: COMMENTS_PAGE_SIZE,
      offset: offset,
    }
    if (commentFilters.label) params.label = commentFilters.label
    const data = await getVideoComments(bvid, params)

    if (append) {
      comments.value = comments.value.concat(data)
    } else {
      comments.value = data
    }
    commentsLoaded.value = comments.value.length

    // 从响应拦截器挂的 __meta 拿 total
    if (data.__meta) commentsTotal.value = data.__meta.total
  } finally {
    commentsLoading.value = false
  }
}

function onFilterChange() {
  // 切换筛选时, 重置到第一页
  commentsLoaded.value = 0
  loadComments(false)
}

// ============ 图表 ============
function renderRadar() {
  if (!radarRef.value || !video.value) return
  radarChart = echarts.init(radarRef.value)
  radarChart.setOption({
    tooltip: {},
    radar: {
      indicator: [
        { name: 'S 情感得分', max: 1 },
        { name: 'C 一致性', max: 1 },
        { name: 'D 讨论深度', max: 1 },
      ],
      radius: '65%',
      splitArea: { areaStyle: { color: ['#fafafa', '#f0f0f0'] } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: [
          // S 是 [-1, 1], 映射到 [0, 1] 显示
          (video.value.s_score + 1) / 2,
          video.value.c_score || 0,
          video.value.d_score || 0,
        ],
        name: '当前视频',
        areaStyle: { color: 'rgba(251, 114, 153, 0.3)' },
        lineStyle: { color: '#fb7299', width: 2 },
        itemStyle: { color: '#fb7299' },
      }],
    }],
  })
}

function renderPie() {
  if (!pieRef.value || !sentiment.value) return
  pieChart = echarts.init(pieRef.value)

  const labelMap = { positive: '正向', negative: '负向', neutral: '中性' }
  const colorMap = { positive: '#67c23a', negative: '#f56c6c', neutral: '#909399' }
  const data = (sentiment.value.labels || []).map(item => ({
    name: labelMap[item.label] || item.label,
    value: item.count,
    itemStyle: { color: colorMap[item.label] },
  }))

  pieChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      label: { formatter: '{b}\n{d}%' },
      data,
    }],
  })
}

// ============ 工具函数 ============
function formatNum(n) {
  if (n == null) return '-'
  if (n >= 100000000) return (n / 100000000).toFixed(1) + '亿'
  if (n >= 10000) return (n / 10000).toFixed(1) + '万'
  return n.toLocaleString()
}
function formatDuration(s) {
  if (!s) return '-'
  const m = Math.floor(s / 60), sec = s % 60
  return `${m}:${String(sec).padStart(2, '0')}`
}
function formatDate(d) {
  if (!d) return '-'
  const date = new Date(d)
  if (isNaN(date.getTime())) return String(d).slice(0, 19).replace('T', ' ')

  const now = new Date()
  const diffMs = now - date
  const diffMin = Math.floor(diffMs / 60000)
  const diffHour = Math.floor(diffMs / 3600000)
  const diffDay = Math.floor(diffMs / 86400000)

  // 相对时间
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  if (diffHour < 24) return `${diffHour} 小时前`
  if (diffDay < 7)  return `${diffDay} 天前`
  if (diffDay < 30) return `${Math.floor(diffDay / 7)} 周前`

  // 超过 30 天用具体日期
  const y = date.getFullYear()
  const m = date.getMonth() + 1
  const d2 = date.getDate()
  if (y === now.getFullYear()) {
    return `${m} 月 ${d2} 日`
  }
  return `${y} 年 ${m} 月 ${d2} 日`
}
function truncate(s, n) {
  return s && s.length > n ? s.slice(0, n) + '...' : s
}
function formatRankChange(c) {
  if (c == null || c === 0) return '— 0'
  return c > 0 ? `↑ ${c}` : `↓ ${-c}`
}
function rankClass(c) {
  if (c > 0) return 'up'
  if (c < 0) return 'down'
  return ''
}
function rankInterpretation(c) {
  if (c > 30) return '反流量党 · 被低估的优质内容'
  if (c < -30) return '流量党 · 评分被高估'
  return '评分相对稳定'
}
function sentimentTagType(label) {
  return { positive: 'success', negative: 'danger', neutral: 'info' }[label] || 'info'
}
function sentimentLabelCN(label) {
  return { positive: '正向', negative: '负向', neutral: '中性' }[label] || label
}

// ============ 生命周期 ============
onMounted(loadAll)
</script>

<style scoped>
.detail-page { max-width: 1400px; margin: 0 auto; }
.back-btn { margin-bottom: 12px; }

.header-card .title { margin: 0 0 8px; font-size: 22px; line-height: 1.4; }
.header-card .meta { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; color: #606266; font-size: 13px; }
.meta-item { color: #606266; }
.ext-link { color: #00aeec; text-decoration: none; margin-left: auto; }
.ext-link:hover { text-decoration: underline; }
.desc { margin: 12px 0 0; color: #909399; font-size: 13px; line-height: 1.6; }

.stats-row { margin-top: 16px; }
.stat-mini { text-align: center; border: none; }
.stat-mini .num { font-size: 22px; font-weight: bold; color: #303133; }
.stat-mini .lbl { color: #909399; font-size: 12px; margin-top: 4px; }

.score-card { margin-top: 16px; }
.score-block { text-align: center; padding: 16px; border-radius: 8px; }
.score-block.static { background: #f0f9ff; }
.score-block.dynamic { background: linear-gradient(135deg, #fff0f6 0%, #f0f9ff 100%); }
.score-block.change { background: #f4f4f5; }
.score-label { color: #606266; font-size: 13px; margin-bottom: 8px; }
.score-value { font-size: 28px; font-weight: bold; font-family: Consolas, monospace; color: #303133; }
.score-value.up { color: #67c23a; }
.score-value.down { color: #f56c6c; }
.score-rank { color: #909399; font-size: 13px; margin-top: 4px; }

.chart-row { margin-top: 16px; }
.chart-box { height: 320px; }
.chart-note { text-align: center; color: #909399; font-size: 12px; margin-top: 8px; line-height: 1.6; }

.comments-card { margin-top: 16px; }
.comments-header { display: flex; align-items: center; justify-content: space-between; }
.comment-filters { display: flex; align-items: center; }
.comments-list { max-height: 600px; overflow-y: auto; }
.comment-item { padding: 12px 0; border-bottom: 1px solid #ebeef5; }
.comment-item:last-child { border-bottom: none; }
.comment-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.uname { font-weight: bold; color: #00aeec; font-size: 13px; }
.like-count { color: #909399; font-size: 12px; margin-left: auto; }
.ctime { color: #909399; font-size: 12px; }
.comment-msg { color: #303133; font-size: 14px; line-height: 1.6; }
.load-more-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 20px 0 8px;
  border-top: 1px dashed #ebeef5;
  margin-top: 8px;
}
.loaded-info {
  color: #909399;
  font-size: 13px;
}
.all-loaded {
  color: #c0c4cc;
  font-size: 13px;
}
</style>
