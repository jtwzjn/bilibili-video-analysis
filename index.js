import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('../views/Home.vue'),
    meta: { title: '首页' },
  },
  {
    path: '/video/:bvid',
    name: 'video-detail',
    component: () => import('../views/VideoDetail.vue'),
    meta: { title: '视频详情' },
  },
  {
    path: '/compare',
    name: 'compare',
    component: () => import('../views/Compare.vue'),
    meta: { title: '模型对比' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
