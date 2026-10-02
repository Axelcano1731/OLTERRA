import { createRouter, createWebHistory } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import { isConnected, loadMe } from '@/session'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    public?: boolean
  }
}

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: '/conectar',
      name: 'connect',
      component: () => import('@/views/ConnectView.vue'),
      meta: { title: 'Entrar', public: true },
    },
    {
      path: '/',
      component: AppShell,
      children: [
        {
          path: '',
          name: 'panel',
          component: () => import('@/views/PanelView.vue'),
          meta: { title: 'Panel' },
        },
        {
          path: 'olts',
          name: 'olts',
          component: () => import('@/views/OltsView.vue'),
          meta: { title: 'OLT' },
        },
        {
          path: 'olts/nueva',
          name: 'olt-new',
          component: () => import('@/views/OltNewView.vue'),
          meta: { title: 'Nueva OLT' },
        },
        {
          path: 'olts/:id',
          name: 'olt',
          component: () => import('@/views/OltDetailView.vue'),
          props: true,
          meta: { title: 'OLT' },
        },
        {
          path: 'tunel',
          name: 'tunnel',
          component: () => import('@/views/TunnelView.vue'),
          meta: { title: 'Túnel' },
        },
        {
          path: 'conciliacion',
          name: 'recons',
          component: () => import('@/views/ReconListView.vue'),
          meta: { title: 'Conciliación' },
        },
        {
          path: 'conciliacion/nueva',
          name: 'recon-new',
          component: () => import('@/views/ReconNewView.vue'),
          meta: { title: 'Nueva conciliación' },
        },
        {
          path: 'conciliacion/:id',
          name: 'recon',
          component: () => import('@/views/ReconDetailView.vue'),
          props: true,
          meta: { title: 'Conciliación' },
        },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: 'No encontrada', public: true },
    },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (!isConnected.value) {
    return { name: 'connect', query: to.fullPath === '/' ? {} : { volver: to.fullPath } }
  }
  try {
    await loadMe()
  } catch {
    // Un 401 ya cerró la sesión; otro error (API caída) lo muestra cada vista.
    if (!isConnected.value) return { name: 'connect', query: { motivo: 'expirada' } }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · Olterra` : 'Olterra'
})
