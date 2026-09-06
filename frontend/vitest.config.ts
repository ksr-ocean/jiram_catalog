import { defineConfig } from 'vitest/config';

// Vitest keeps its own Vite; the unit tests are pure functions and the store,
// so they need neither the React plugin nor a DOM.
export default defineConfig({
  test: {
    environment: 'node',
    globals: true,
    include: ['tests/**/*.test.ts'],
  },
});
