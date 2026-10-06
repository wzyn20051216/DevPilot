import { createRouter, createWebHistory } from 'vue-router'

import TaskDetailView from '../views/TaskDetailView.vue'
import WorkspaceView from '../views/WorkspaceView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'workspace', component: WorkspaceView },
    { path: '/tasks/:taskId', name: 'task-detail', component: TaskDetailView },
  ],
})

export default router
