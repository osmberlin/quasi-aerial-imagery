import { defineConfig } from 'oxfmt'

// Single quotes; ignorePatterns are kept in sync with oxlint.config.mjs.
export default defineConfig({
  useTabs: false,
  tabWidth: 2,
  printWidth: 100,
  singleQuote: true,
  jsxSingleQuote: false,
  quoteProps: 'as-needed',
  trailingComma: 'all',
  semi: false,
  arrowParens: 'always',
  bracketSameLine: false,
  bracketSpacing: true,
  endOfLine: 'lf',
  sortImports: {
    newlinesBetween: false,
  },
  sortTailwindcss: {
    stylesheet: 'src/index.css',
    functions: ['twMerge', 'twJoin', 'clsx'],
  },
  sortPackageJson: true,
  ignorePatterns: [
    '.agents/**',
    '.cursor/**',
    '.output/**',
    'playwright-report/**',
    'test-results/**',
    'src/routeTree.gen.ts',
    'src/prisma/generated/**',
  ],
})
