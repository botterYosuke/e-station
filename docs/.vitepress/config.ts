import { defineConfig } from 'vitepress'

export default defineConfig({
  lang: 'ja',
  title: 'e-station',
  description: 'e-station エンジニア向けドキュメント',
  base: '/e-station/',

  srcExclude: ['plan/**', 'wiki/**'],

  ignoreDeadLinks: true,

  lastUpdated: true,

  themeConfig: {
    siteTitle: 'e-station dev',

    nav: [
      { text: 'GitHub', link: 'https://github.com/botterYosuke/e-station' },
    ],

    sidebar: {
      '/spec/': [
        {
          text: '実装仕様書',
          items: [
            { text: 'Python データエンジン', link: 'plan/✅python-data-engine/spec.md' },
            { text: '立花証券 API 統合', link: 'plan/✅tachibana/spec.md' },
            { text: '立花注文機能', link: 'plan/✅order/spec.md' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/botterYosuke/e-station' },
    ],

    footer: {
      message: 'Released under the GPL-3.0 License.',
    },

    outline: {
      label: '目次',
      level: [2, 3],
    },

    docFooter: {
      prev: '前のページ',
      next: '次のページ',
    },

    lastUpdated: {
      text: '最終更新',
    },

    search: {
      provider: 'local',
      options: {
        locales: {
          root: {
            translations: {
              button: {
                buttonText: '検索',
                buttonAriaLabel: 'ドキュメントを検索',
              },
              modal: {
                noResultsText: '検索結果が見つかりません',
                resetButtonTitle: '検索をリセット',
                footer: {
                  selectText: '選択',
                  navigateText: '移動',
                  closeText: '閉じる',
                },
              },
            },
          },
        },
      },
    },
  },
})
