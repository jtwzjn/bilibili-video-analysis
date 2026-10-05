<template>
  <div class="home">
    <!-- 顶部统计大数字 -->
    <el-row :gutter="20" class="overview-row">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-blue">
          <div class="stat-num">{{ overview.total_videos || '-' }}</div>
          <div class="stat-label">视频总数</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-pink">
          <div class="stat-num">{{ formatNum(overview.total_comments) }}</div>
          <div class="stat-label">评论总数</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-green">
          <div class="stat-num">{{ overview.total_categories || '-' }}</div>
          <div class="stat-label">分区数量</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-orange">
          <div class="stat-num">{{ (overview.avg_q || 0).toFixed(3) }}</div>
          <div class="stat-label">平均质量系数 Q</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 控制栏 -->
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <span class="filter-label">分类:</span>
        <el-radio-group v-model="filters.category" @change="loadList">
          <el-radio-button label="">全部</el-radio-button>
          <el-radio-button
            v-for="c in categories"
            :key="c.category"
            :label="c.category"
          >{{ c.category }} ({{ c.video_count }})</el-radio-button>
        </el-radio-group>
      </div>
      <div class="filter-row">
        <span class="filter-label">评分模式:</span>
        <el-radio-group v-model="filters.mode" @change="loadList">
          <el-radio-button label="dynamic">动态评分 (RoBERTa 加权)</el-radio-button>
          <el-radio-button label="static">静态评分 (基线)</el-radio-button>
        </el-radio-group>
      </div>
    </el-card>

    <!-- 排行榜表格 -->
    <el-card shadow="never" class="table-card">
      <el-table
        v-loading="loading"
        :data="tableData"
        stripe
        @row-click="goDetail"
      >
        <el-table-column type="index" label="排名" width="60" :index="indexFn" />
        <el-table-column label="标题" min-width="280">
          <template #default="{ row }">
            <div class="title-cell">
              <el-tag size="small" effect="plain" class="category-tag">
                {{ row.category }}
              </el-tag>
              <span class="title-text">{{ row.title }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="owner_name" label="UP主" width="140" />
        <el-table-column label="播放" width="110" sortable>
          <template #default="{ row }">{{ formatNum(row.view_count) }}</template>
        </el-table-column>
        <el-table-column label="点赞" width="100">
          <template #default="{ row }">{{ formatNum(row.like_count) }}</template>
        </el-table-column>
        <el-table-column label="质量系数 Q" width="130" sortable>
          <template #default="{ row }">
            <div class="q-cell">
              <el-progress
                :percentage="Math.round(row.quality_q * 100)"
                :stroke-width="8"
                :show-text="false"
                :color="qColor(row.quality_q)"
              />
              <span class="q-num">{{ (row.quality_q || 0).toFixed(3) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="排名变化" width="110">
          <template #default="{ row }">
            <span :class="rankChangeClass(row.rank_change)">
              {{ formatRankChange(row.rank_change) }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next, total"
        class="pagination"
        @current-change="onPageChange"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getOverview, getCategories, getVideoRank } from '../api'

const router = useRouter()

// 状态
const overview = ref({})
const categories = ref([])
const tableData = ref([])
const loading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = 20

const filters = reactive({
  category: '',
  mode: 'dynamic',
})

// ============= 加载数据 =============
async function loadOverview() {
  try {
    const data = await getOverview()
    overview.value = data.overview || {}
  } catch (e) { console.error(e) }
}

async function loadCategories() {
  try {
    categories.value = await getCategories()
  } catch (e) { console.error(e) }
}

async function loadList() {
  loading.value = true
  try {
    const params = {
      mode: filters.mode,
      limit: pageSize,
      offset: (currentPage.value - 1) * pageSize,
    }
    if (filters.category) params.category = filters.category
    const data = await getVideoRank(params)
    tableData.value = data
    if (data.__meta) total.value = data.__meta.total
  } finally {
    loading.value = false
  }
}

function onPageChange(p) {
  currentPage.value = p
  loadList()
}

// ============= 工具函数 =============
function indexFn(i) {
  return (currentPage.value - 1) * pageSize + i + 1
}

function formatNum(n) {
  if (n == null) return '-'
  if (n >= 100000000) return (n / 100000000).toFixed(1) + '亿'
  if (n >= 10000) return (n / 10000).toFixed(1) + '万'
  return n.toLocaleString()
}

function qColor(q) {
  if (q >= 0.7) return '#67c23a'
  if (q >= 0.55) return '#e6a23c'
  return '#f56c6c'
}

function formatRankChange(c) {
  if (c == null || c === 0) return '— 0'
  return c > 0 ? `↑ ${c}` : `↓ ${-c}`
}
function rankChangeClass(c) {
  if (c > 0) return 'rank-up'
  if (c < 0) return 'rank-down'
  return 'rank-zero'
}

function goDetail(row) {
  router.push(`/video/${row.bvid}`)
}

// ============= 初始化 =============
onMounted(() => {
  loadOverview()
  loadCategories()
  loadList()
})
</script>

<style scoped>
.home { max-width: 1400px; margin: 0 auto; }

.overview-row { margin-bottom: 20px; }
.stat-card {
  text-align: center;
  border: none;
  cursor: default;
}
.stat-card .stat-num {
  font-size: 32px;
  font-weight: bold;
  color: #303133;
  margin: 8px 0;
}
.stat-card .stat-label {
  color: #909399;
  font-size: 14px;
}
.stat-blue .stat-num { color: #00aeec; }
.stat-pink .stat-num { color: #fb7299; }
.stat-green .stat-num { color: #67c23a; }
.stat-orange .stat-num { color: #e6a23c; }

.filter-card { margin-bottom: 16px; }
.filter-row {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.filter-row:last-child { margin-bottom: 0; }
.filter-label {
  width: 80px;
  color: #606266;
  font-size: 14px;
}

.table-card .el-table {
  cursor: pointer;
}
.title-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.category-tag { flex-shrink: 0; }
.title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.q-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.q-num {
  font-family: Consolas, monospace;
  font-size: 12px;
  color: #606266;
}
.rank-up { color: #67c23a; font-weight: bold; }
.rank-down { color: #f56c6c; font-weight: bold; }
.rank-zero { color: #909399; }

.pagination {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
