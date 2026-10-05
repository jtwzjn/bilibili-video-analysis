import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({
  baseURL: '/api',
  timeout: 90000,   // 分析新视频可能需要 30-60 秒, 给宽松点
})

api.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && body.code === 0) {
      const result = body.data
      if (body.total !== undefined) {
        result.__meta = { total: body.total, limit: body.limit, offset: body.offset }
      }
      result.__extras = {
        from_cache: body.from_cache,
        elapsed_seconds: body.elapsed_seconds,
      }
      return result
    }
    ElMessage.error(body.msg || '请求失败')
    return Promise.reject(new Error(body.msg))
  },
  (err) => {
    const msg = err.response?.data?.msg || err.message || '网络错误'
    ElMessage.error(msg)
    return Promise.reject(err)
  }
)

export const getOverview       = () => api.get('/stats/overview')
export const getCategories     = () => api.get('/categories')
export const getVideoRank      = (params) => api.get('/videos/rank', { params })
export const getVideoDetail    = (bvid) => api.get(`/video/${bvid}`)
export const getVideoSentiment = (bvid) => api.get(`/video/${bvid}/sentiment`)
export const getVideoComments  = (bvid, params) => api.get(`/video/${bvid}/comments`, { params })
export const getCompare        = (params) => api.get('/compare', { params })

// 新增
export const analyzeNewVideo   = (input) => api.post('/analyze', { input })
export const rerankAll         = () => api.post('/admin/rerank')

export default api
