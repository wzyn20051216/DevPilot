import { createRouter, createWebHistory } from 'vue-router'

import TaskDetailView from '../views/TaskDetailView.vue'
import WorkspaceView from '../views/WorkspaceView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'workspace', component: WorkspaceView },
    {
      path: '/evaluations',
      name: 'evaluations',
      // ECharts 只在评测页需要，按路由懒加载可避免拖慢工作台首屏。
      component: () => import('../views/EvaluationView.vue'),
    },
    { path: '/tasks/:taskId', name: 'task-detail', component: TaskDetailView },
  ],
})

export default router
