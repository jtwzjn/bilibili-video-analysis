import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'home', component: () => import('../views/Home.vue') },
  { path: '/video/:bvid', name: 'video-detail', component: () => import('../views/VideoDetail.vue') },
  { path: '/compare', name: 'compare', component: () => import('../views/Compare.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
