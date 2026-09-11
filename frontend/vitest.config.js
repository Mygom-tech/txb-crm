import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  // Compile single-file components so component-mounted interaction tests (e.g.
  // DateTimeWithOptions) can import and mount real `.vue` controls.
  plugins: [vue()],
  test: {
    globals: true,
    environment: 'happy-dom',
    root: __dirname,
    setupFiles: ['./tests/setup.js'],
    include: ['tests/**/*.test.js', 'src/**/*.test.js'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'json-summary'],
      reportsDirectory: './coverage',
      include: [
        'src/utils/fieldTransforms.js',
        'src/utils/scriptHelpers.js',
        'src/utils/expressions.js',
        'src/utils/renderFieldLayoutDialog.js',
        'src/utils/kanbanRevert.js',
        'src/utils/kanbanTransitions.js',
        'src/utils/pipelineStatuses.js',
        'src/utils/takeAction.js',
        'src/utils/dealTransitions.js',
        'src/utils/leadReasonPrompt.js',
        'src/utils/organizationLifecycle.js',
      ],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
})
