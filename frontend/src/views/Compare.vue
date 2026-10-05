<template>
  <div class="compare-page">
    <!-- 说明 -->
    <el-card shadow="never" class="intro-card">
      <h2 class="intro-title">动态权重综合评分模型对比</h2>
      <p class="intro-text">
        传统视频排序通常依赖 <b>播放量</b>、<b>点赞</b>、<b>投币</b> 等静态指标的固定加权。本系统通过 <b>RoBERTa 中文情感模型</b> 对 {{ stats.total_comments || '12 万' }} 条评论进行分析，
        计算出每个视频的 <b>评论质量系数 Q</b>（综合情感得分、一致性、讨论深度），并据此动态调整各指标的权重。
      </p>
      <el-row :gutter="16" class="formula-row">
        <el-col :span="12">
          <div class="formula-box static-formula">
            <div class="formula-title">静态评分（基线）</div>
            <code>Score = 0.30·view + 0.25·like + 0.20·coin + 0.15·fav + 0.10·share</code>
          </div>
        </el-col>
        <el-col :span="12">
          <div class="formula-box dynamic-formula">
            <div class="formula-title">动态评分（本方案）</div>
            <code>W_view = 0.30·(1 − 0.3Q)　　// 高 Q 降低播放权重</code><br>
            <code>W_coin = 0.20·(1 + 0.2Q)　　// 高 Q 提升硬核认可权重</code><br>
            <code>+ 0.20·Q　　　　　　　　　　// Q 本身作为新维度</code>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 上升组 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="section-header">
          <span class="section-title">📈 排名上升最多 — 反流量党，被低估的优质内容</span>
          <el-tag type="success" size="small">动态评分 > 静态评分</el-tag>
        </div>
      </template>
      <div ref="risersChartRef" class="chart-large"></div>
      <el-table :data="risers" stripe @row-click="goDetail" class="data-table">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column label="标题" min-width="300">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" class="cat-tag">{{ row.category }}</el-tag>
            {{ row.title }}
          </template>
        </el-table-column>
        <el-table-column label="播放量" width="100">
          <template #default="{ row }">{{ formatNum(row.view_count) }}</template>
        </el-table-column>
        <el-table-column label="Q" width="80">
          <template #default="{ row }">{{ (row.quality_q || 0).toFixed(3) }}</template>
        </el-table-column>
        <el-table-column label="静态排名" width="100" prop="rank_static" />
        <el-table-column label="动态排名" width="100" prop="rank_dynamic" />
        <el-table-column label="变化" width="80">
          <template #default="{ row }">
            <span class="rank-up">↑ {{ row.rank_change }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 下降组 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="section-header">
          <span class="section-title">📉 排名下降最多 — 流量党识别，评分被高估</span>
          <el-tag type="danger" size="small">动态评分 < 静态评分</el-tag>
        </div>
      </template>
      <div ref="fallersChartRef" class="chart-large"></div>
      <el-table :data="fallers" stripe @row-click="goDetail" class="data-table">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column label="标题" min-width="300">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" class="cat-tag">{{ row.category }}</el-tag>
            {{ row.title }}
          </template>
        </el-table-column>
        <el-table-column label="播放量" width="100">
          <template #default="{ row }">{{ formatNum(row.view_count) }}</template>
        </el-table-column>
        <el-table-column label="Q" width="80">
          <template #default="{ row }">{{ (row.quality_q || 0).toFixed(3) }}</template>
        </el-table-column>
        <el-table-column label="静态排名" width="100" prop="rank_static" />
        <el-table-column label="动态排名" width="100" prop="rank_dynamic" />
        <el-table-column label="变化" width="80">
          <template #default="{ row }">
            <span class="rank-down">↓ {{ -row.rank_change }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 散点图: 全样本 Q vs 排名变化 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="section-header">
          <span class="section-title">🔬 全样本散点图: 质量系数 Q × 排名变化</span>
          <span class="section-hint">每个点是一个视频；可观察 Q 越高排名越倾向上升的趋势</span>
        </div>
      </template>
      <div ref="scatterRef" class="chart-large"></div>
    </el-card>

    <!-- 各分类平均 Q -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <span class="section-title">📊 各分区平均质量系数 Q</span>
      </template>
      <div ref="categoryChartRef" class="chart-large"></div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { getCompare, getCategories, getOverview, getVideoRank } from '../api'

const router = useRouter()

const stats = ref({})
const risers = ref([])
const fallers = ref([])
const allVideos = ref([])
const categories = ref([])

const risersChartRef = ref(null)
const fallersChartRef = ref(null)
const scatterRef = ref(null)
const categoryChartRef = ref(null)

async function loadAll() {
  // 并发拉数据
  const [overview, up, down, cats] = await Promise.all([
    getOverview(),
    getCompare({ direction: 'up', limit: 15 }),
    getCompare({ direction: 'down', limit: 15 }),
    getCategories(),
  ])
  stats.value = overview.overview || {}
  risers.value = up
  fallers.value = down
  categories.value = cats

  // 拉全样本 (用于散点图)
  // 后端没有 "返回全部" 接口, 用 limit=1000 一次拉完
  const all = await getVideoRank({ mode: 'dynamic', limit: 1000 })
  allVideos.value = all

  await nextTick()
  renderRankCompareChart(risersChartRef.value, risers.value, 'up')
  renderRankCompareChart(fallersChartRef.value, fallers.value, 'down')
  renderScatter()
  renderCategoryChart()
}

// ============ 排名对比柱状图 ============
function renderRankCompareChart(el, data, mode) {
  if (!el) return
  const chart = echarts.init(el)
  const titles = data.map(d => truncate(d.title, 18))

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const idx = params[0].dataIndex
        const item = data[idx]
        return `
          <b>${item.title}</b><br>
          分区: ${item.category}<br>
          播放: ${formatNum(item.view_count)}<br>
          Q: ${(item.quality_q || 0).toFixed(3)}<br>
          静态排名: #${item.rank_static}<br>
          动态排名: #${item.rank_dynamic}<br>
          <b>${mode === 'up' ? '↑ 上升' : '↓ 下降'} ${Math.abs(item.rank_change)} 名</b>
        `
      },
    },
    legend: { data: ['静态排名', '动态排名'], top: 0 },
    // 关键 1: bottom 给到 18%, 留足空间放旋转标签 + 横轴名称
    grid: { left: '6%', right: '4%', bottom: '18%', top: '12%', containLabel: true },
    xAxis: {
      type: 'category',
      data: titles,
      // 关键 2: 加横轴名称, 放在右侧 (end), 避免挡住居中的视频名
      name: '视频',
      nameLocation: 'end',
      nameGap: 15,
      nameTextStyle: { fontSize: 12, fontWeight: 'bold', color: '#606266' },
      axisLabel: {
        interval: 0,
        rotate: 35,
        fontSize: 11,
        // 关键 3: margin 给 12, 让标签和轴线有间距, 视频名不会和轴名挤在一起
        margin: 12,
      },
    },
    yAxis: {
      type: 'value',
      name: '排名',
      nameLocation: 'end',
      nameGap: 15,
      nameTextStyle: { fontSize: 12, fontWeight: 'bold', color: '#606266' },
      inverse: true,
    },
    series: [
      {
        name: '静态排名',
        type: 'bar',
        data: data.map(d => d.rank_static),
        itemStyle: { color: '#909399' },
        barGap: 0,
      },
      {
        name: '动态排名',
        type: 'bar',
        data: data.map(d => d.rank_dynamic),
        itemStyle: { color: mode === 'up' ? '#67c23a' : '#f56c6c' },
      },
    ],
  })
}

// ============ 散点图 Q vs 排名变化 ============
function renderScatter() {
  if (!scatterRef.value) return
  const chart = echarts.init(scatterRef.value)

  // 按分类分组着色
  const categoryColors = {
    '音乐': '#fb7299', '影视': '#722ed1', '动画': '#13c2c2', '知识': '#1890ff',
    '生活': '#52c41a', '美食': '#fa8c16', '游戏': '#eb2f96', '科技': '#2f54eb',
  }
  const grouped = {}
  allVideos.value.forEach(v => {
    if (!grouped[v.category]) grouped[v.category] = []
    grouped[v.category].push([v.quality_q, v.rank_change, v.title])
  })

  chart.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        return `<b>${p.value[2]}</b><br>分区: ${p.seriesName}<br>Q: ${p.value[0].toFixed(3)}<br>排名变化: ${p.value[1] > 0 ? '↑' : '↓'} ${Math.abs(p.value[1])}`
      },
    },
    legend: { top: 0, type: 'scroll' },
    grid: { left: '3%', right: '4%', bottom: '6%', top: '12%', containLabel: true },
    xAxis: {
      type: 'value',
      name: '质量系数 Q',
      nameLocation: 'middle',
      nameGap: 30,
      min: 0.2,
      max: 0.85,
    },
    yAxis: {
      type: 'value',
      name: '排名变化 (正=上升)',
      nameLocation: 'middle',
      nameGap: 40,
    },
    series: Object.entries(grouped).map(([cat, points]) => ({
      name: cat,
      type: 'scatter',
      symbolSize: 8,
      data: points,
      itemStyle: { color: categoryColors[cat] || '#999', opacity: 0.7 },
    })),
  })
}

// ============ 各分区平均 Q ============
function renderCategoryChart() {
  if (!categoryChartRef.value) return
  const chart = echarts.init(categoryChartRef.value)
  const sorted = [...categories.value].sort((a, b) => (b.avg_q || 0) - (a.avg_q || 0))

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      // 关键 4: tooltip 也用三位小数格式化
      formatter: (params) => {
        const p = params[0]
        return `<b>${p.name}</b><br>平均 Q: ${Number(p.value).toFixed(3)}`
      },
    },
    grid: { left: '6%', right: '4%', bottom: '12%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: sorted.map(c => c.category),
      // 关键 1: 横轴加分区名称
      name: '分区',
      nameLocation: 'end',
      nameGap: 15,
      nameTextStyle: { fontSize: 12, fontWeight: 'bold', color: '#606266' },
      axisLabel: { fontSize: 12, margin: 10 },
    },
    yAxis: {
      type: 'value',
      name: '平均 Q',
      nameLocation: 'end',
      nameGap: 15,
      nameTextStyle: { fontSize: 12, fontWeight: 'bold', color: '#606266' },
      // 关键 2: 基准线调到 0.450
      min: 0.450,
      max: 0.700,
      // 关键 3: 纵轴刻度三位小数
      axisLabel: {
        formatter: (val) => Number(val).toFixed(3),
      },
    },
    series: [{
      type: 'bar',
      // 关键 5: data 的 value 用 number 而不是字符串, formatter 里再转字符串
      data: sorted.map(c => ({
        value: Number(c.avg_q || 0),
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#fb7299' },
            { offset: 1, color: '#00aeec' },
          ]),
        },
      })),
      label: {
        show: true,
        position: 'top',
        // 关键 6: 柱顶标签强制三位小数
        formatter: (params) => Number(params.value).toFixed(3),
        fontSize: 12,
      },
      barWidth: '50%',
    }],
  })
}

// ============ 工具 ============
function formatNum(n) {
  if (n == null) return '-'
  if (n >= 100000000) return (n / 100000000).toFixed(1) + '亿'
  if (n >= 10000) return (n / 10000).toFixed(1) + '万'
  return n.toLocaleString()
}
function truncate(s, n) {
  return s && s.length > n ? s.slice(0, n) + '..' : s
}
function goDetail(row) {
  router.push(`/video/${row.bvid}`)
}

onMounted(loadAll)
</script>

<style scoped>
.compare-page { max-width: 1400px; margin: 0 auto; }

.intro-card { margin-bottom: 16px; }
.intro-title { margin: 0 0 12px; font-size: 22px; }
.intro-text { color: #606266; line-height: 1.7; margin: 0 0 16px; }

.formula-row { margin-top: 8px; }
.formula-box {
  padding: 12px 16px;
  border-radius: 8px;
  font-family: Consolas, monospace;
  font-size: 12px;
  line-height: 1.8;
}
.static-formula { background: #f4f4f5; color: #606266; }
.dynamic-formula { background: linear-gradient(135deg, #fff0f6, #f0f9ff); color: #303133; }
.formula-title { font-family: -apple-system, sans-serif; font-weight: bold; margin-bottom: 6px; font-size: 13px; }

.section-card { margin-bottom: 16px; }
.section-header {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.section-title { font-weight: bold; font-size: 15px; }
.section-hint { color: #909399; font-size: 12px; }

.chart-large { height: 380px; margin-bottom: 12px; }

.data-table { cursor: pointer; }
.cat-tag { margin-right: 8px; }
.rank-up { color: #67c23a; font-weight: bold; }
.rank-down { color: #f56c6c; font-weight: bold; }
</style>
