import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'

export default defineConfigWithVueTs(
  { name: 'olterra/ignores', ignores: ['dist/**', 'node_modules/**'] },
  pluginVue.configs['flat/recommended'],
  vueTsConfigs.recommendedTypeChecked,
  {
    name: 'olterra/rules',
    rules: {
      // Todo texto llega escapado; v-html abriría la puerta a inyectar HTML desde la API.
      'vue/no-v-html': 'error',
      'vue/multi-word-component-names': 'off',
    },
  },
  // El formato lo pone Prettier (npm run format); ESLint solo mira errores.
  skipFormatting,
)
