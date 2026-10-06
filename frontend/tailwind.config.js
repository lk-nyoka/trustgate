/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        sidebar:  '#0f1117',
        'sidebar-border': '#1e2230',
        surface:  '#ffffff',
        surface2: '#f8f9fb',
        acc:      '#2563eb',
        'acc-light': '#dbeafe',
        'acc-dark':  '#1d4ed8',
        ok:       '#059669',
        'ok-light':  '#d1fae5',
        'ok-dark':   '#065f46',
        warn:     '#d97706',
        'warn-light': '#fef3c7',
        'warn-dark':  '#92400e',
        bad:      '#dc2626',
        'bad-light':  '#fee2e2',
        'bad-dark':   '#7f1d1d',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'Menlo', 'Consolas', 'monospace'],
      },
      animation: {
        'pulse-dot': 'pulse-dot 2s infinite',
        'rise': 'rise 0.35s ease forwards',
      },
      keyframes: {
        'pulse-dot': {
          '0%,100%': { opacity: '1' },
          '50%':     { opacity: '0.4' },
        },
        rise: {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to:   { opacity: '1', transform: 'none' },
        },
      },
    },
  },
  plugins: [],
}
