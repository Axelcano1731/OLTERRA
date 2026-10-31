<script setup lang="ts">
import {
  Bell,
  GitCompareArrows,
  House,
  LayoutTemplate,
  LogOut,
  MapPin,
  Menu,
  PlugZap,
  ScrollText,
  Search,
  Server,
  Smartphone,
  UserCog,
  Users,
  Waypoints,
  X,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch, type Component } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter, type RouteLocationRaw } from 'vue-router'

import { getMonitoringSummary } from '@/api'
import { can, session, signOut } from '@/session'

import Logo from './Logo.vue'

interface NavItem {
  to: RouteLocationRaw
  label: string
  icon: Component
  routes: string[]
  /** Permiso que hace falta para verlo. */
  scope?: string
  /** Lleva el número de alarmas abiertas. */
  alarms?: boolean
}

// Primero lo del día a día; la red y los ajustes, que se tocan poco, abajo.
const GROUPS: { title?: string; items: NavItem[] }[] = [
  {
    items: [
      { to: { name: 'panel' }, label: 'Inicio', icon: House, routes: ['panel'] },
      { to: { name: 'clients' }, label: 'Clientes', icon: Users, routes: ['clients', 'onu'] },
      {
        to: { name: 'provision' },
        label: 'Autorizar ONU',
        icon: PlugZap,
        routes: ['provision', 'olt-provision'],
      },
      { to: { name: 'alarms' }, label: 'Alarmas', icon: Bell, routes: ['alarms'], alarms: true },
      { to: { name: 'map' }, label: 'Mapa FTTH', icon: MapPin, routes: ['map'] },
    ],
  },
  {
    title: 'Red',
    items: [
      {
        to: { name: 'olts' },
        label: 'OLT',
        icon: Server,
        routes: ['olts', 'olt-new', 'olt', 'olt-edit', 'olt-password', 'olt-backups'],
      },
      { to: { name: 'tunnel' }, label: 'Túnel', icon: Waypoints, routes: ['tunnel'] },
    ],
  },
  {
    title: 'Ajustes',
    items: [
      {
        to: { name: 'templates' },
        label: 'Planes',
        icon: LayoutTemplate,
        routes: ['templates', 'template-new', 'template-edit'],
      },
      {
        to: { name: 'users' },
        label: 'Usuarios',
        icon: UserCog,
        routes: ['users'],
        scope: 'users:read',
      },
      {
        to: { name: 'recons' },
        label: 'Conciliación',
        icon: GitCompareArrows,
        routes: ['recons', 'recon-new', 'recon'],
      },
      {
        to: { name: 'audit' },
        label: 'Bitácora',
        icon: ScrollText,
        routes: ['audit'],
        scope: 'audit:read',
      },
    ],
  },
]

// Lo que viene según el plan (docs/ARQUITECTURA.md): se ve, pero no se puede abrir.
const SOON = [{ label: 'App de campo', icon: Smartphone, phase: 3 }]

const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const groups = computed(() =>
  GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.scope || can(item.scope)),
  })).filter((group) => group.items.length),
)

// Alarmas abiertas en el menú: se ven desde cualquier página.
const openAlarms = ref({ critical: 0, total: 0 })
async function loadAlarms(): Promise<void> {
  try {
    const summary = await getMonitoringSummary()
    openAlarms.value = { critical: summary.critical, total: summary.critical + summary.warning }
  } catch {
    // Sin el número no pasa nada: la página de alarmas dice lo que haya.
  }
}
let alarmsTimer: number | undefined
onMounted(() => {
  void loadAlarms()
  alarmsTimer = window.setInterval(() => void loadAlarms(), 60_000)
})
onBeforeUnmount(() => window.clearInterval(alarmsTimer))

// Buscar un cliente desde cualquier página: lleva a Clientes con la búsqueda puesta.
const search = ref('')
function find(): void {
  const q = search.value.trim()
  if (!q) return
  search.value = ''
  void router.push({ name: 'clients', query: { q } })
}

watch(
  () => route.fullPath,
  () => (menuOpen.value = false),
)

function isActive(item: NavItem): boolean {
  return item.routes.includes(String(route.name))
}

async function logout(): Promise<void> {
  await signOut()
  await router.push({ name: 'connect' })
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

      <form class="px-3 pb-2" role="search" @submit.prevent="find">
        <label class="relative block">
          <span class="sr-only">Buscar cliente</span>
          <Search
            class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-sidebar-muted"
          />
          <input
            v-model="search"
            type="search"
            class="w-full rounded-lg border border-white/10 bg-sidebar-hover py-2 pr-3 pl-9 text-sm text-white placeholder:text-sidebar-muted focus:border-white/30 focus:outline-none"
            placeholder="Buscar cliente o serial"
          />
        </label>
      </form>

      <nav class="flex-1 overflow-y-auto px-3 py-2" aria-label="Principal">
        <div v-for="(group, index) in groups" :key="group.title ?? index" class="mb-3">
          <p
            v-if="group.title"
            class="px-3 pt-2 pb-1 text-[11px] font-semibold tracking-wider text-sidebar-muted/70 uppercase"
          >
            {{ group.title }}
          </p>
          <div class="space-y-1">
            <RouterLink
              v-for="item in group.items"
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
              <span
                v-if="item.alarms && openAlarms.total"
                :class="[
                  'ml-auto rounded-full px-1.5 py-0.5 text-[11px] leading-none font-semibold text-white',
                  openAlarms.critical ? 'bg-red-600' : 'bg-amber-600',
                ]"
                :aria-label="`${openAlarms.total} alarmas abiertas`"
              >
                {{ openAlarms.total }}
              </span>
            </RouterLink>
          </div>
        </div>

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
          {{ session.me?.user?.display_name ?? session.me?.tenant.name ?? '…' }}
        </p>
        <p class="truncate text-xs text-sidebar-muted">
          <template v-if="session.me?.user">
            {{ session.me.tenant.name }} ·
            <RouterLink :to="{ name: 'change-password' }" class="hover:text-white hover:underline">
              cambiar contraseña
            </RouterLink>
          </template>
          <template v-else>Llave: {{ session.me?.key_name ?? '…' }}</template>
        </p>
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
