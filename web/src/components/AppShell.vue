<script setup lang="ts">
import {
  Activity,
  GitCompareArrows,
  LayoutTemplate,
  LayoutDashboard,
  LogOut,
  MapPin,
  Menu,
  Server,
  Waypoints,
  X,
} from '@lucide/vue'
import { ref, watch, type Component } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter, type RouteLocationRaw } from 'vue-router'

import { disconnect, session } from '@/session'

import Logo from './Logo.vue'

interface NavItem {
  to: RouteLocationRaw
  label: string
  icon: Component
  routes: string[]
}

const NAV: NavItem[] = [
  { to: { name: 'panel' }, label: 'Panel', icon: LayoutDashboard, routes: ['panel'] },
  {
    to: { name: 'olts' },
    label: 'OLT',
    icon: Server,
    routes: ['olts', 'olt-new', 'olt', 'olt-edit', 'olt-provision'],
  },
  {
    to: { name: 'templates' },
    label: 'Plantillas',
    icon: LayoutTemplate,
    routes: ['templates', 'template-new', 'template-edit'],
  },
  { to: { name: 'tunnel' }, label: 'Túnel', icon: Waypoints, routes: ['tunnel'] },
  {
    to: { name: 'recons' },
    label: 'Conciliación',
    icon: GitCompareArrows,
    routes: ['recons', 'recon-new', 'recon'],
  },
]

// Lo que viene según el plan (docs/ARQUITECTURA.md): se ve, pero no se puede abrir.
const SOON = [
  { label: 'Monitoreo', icon: Activity, phase: 1 },
  { label: 'Mapa FTTH', icon: MapPin, phase: 2 },
]

const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)

watch(
  () => route.fullPath,
  () => (menuOpen.value = false),
)

function isActive(item: NavItem): boolean {
  return item.routes.includes(String(route.name))
}

function logout(): void {
  disconnect()
  void router.push({ name: 'connect' })
}
</script>

<template>
  <div class="min-h-dvh lg:pl-64">
    <header
      class="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-surface/90 px-4 backdrop-blur lg:hidden"
    >
      <RouterLink :to="{ name: 'panel' }" class="text-ink"><Logo /></RouterLink>
      <button type="button" class="btn-ghost p-2" aria-label="Abrir menú" @click="menuOpen = true">
        <Menu class="size-5" />
      </button>
    </header>

    <div
      v-if="menuOpen"
      class="fixed inset-0 z-40 bg-black/40 lg:hidden"
      aria-hidden="true"
      @click="menuOpen = false"
    />

    <aside
      :class="[
        'fixed inset-y-0 left-0 z-50 flex w-64 flex-col bg-sidebar text-sidebar-ink transition-transform duration-200 lg:translate-x-0',
        menuOpen ? 'translate-x-0' : '-translate-x-full',
      ]"
    >
      <div class="flex h-16 items-center justify-between px-5">
        <RouterLink :to="{ name: 'panel' }" class="text-white"><Logo /></RouterLink>
        <button
          type="button"
          class="rounded-md p-1.5 text-sidebar-muted hover:bg-sidebar-hover lg:hidden"
          aria-label="Cerrar menú"
          @click="menuOpen = false"
        >
          <X class="size-5" />
        </button>
      </div>

      <nav class="flex-1 space-y-1 overflow-y-auto px-3 py-2" aria-label="Principal">
        <RouterLink
          v-for="item in NAV"
          :key="item.label"
          :to="item.to"
          :aria-current="isActive(item) ? 'page' : undefined"
          :class="[
            'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
            isActive(item)
              ? 'bg-sidebar-active text-white'
              : 'text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-ink',
          ]"
        >
          <component :is="item.icon" class="size-4.5" />
          {{ item.label }}
        </RouterLink>

        <p
          class="px-3 pt-6 pb-2 text-[11px] font-semibold tracking-wider text-sidebar-muted/70 uppercase"
        >
          Próximamente
        </p>
        <span
          v-for="item in SOON"
          :key="item.label"
          class="flex cursor-default items-center gap-3 rounded-lg px-3 py-2 text-sm text-sidebar-muted/60"
        >
          <component :is="item.icon" class="size-4.5" />
          {{ item.label }}
          <span class="ml-auto rounded bg-sidebar-hover px-1.5 py-0.5 text-[10px]">
            Fase {{ item.phase }}
          </span>
        </span>
      </nav>

      <div class="border-t border-white/10 p-4">
        <p class="truncate text-sm font-medium text-white">
          {{ session.me?.tenant.name ?? '…' }}
        </p>
        <p class="truncate text-xs text-sidebar-muted">Llave: {{ session.me?.key_name ?? '…' }}</p>
        <button
          type="button"
          class="mt-3 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-sidebar-muted hover:bg-sidebar-hover hover:text-white"
          @click="logout"
        >
          <LogOut class="size-4" />
          Salir
        </button>
      </div>
    </aside>

    <main class="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
      <RouterView />
    </main>
  </div>
</template>
