import { defineConfig } from 'vitepress'

export default defineConfig({
  lang: 'ja',
  title: 'flowsurface',
  description: 'Rust 製デスクトップチャートアプリ — 操作ガイド',
  base: '/flowsurface/',

  // plan/ は開発者向け作業ドキュメントのため除外
  srcExclude: ['plan/**'],

  // plan/ は srcExclude で除外済み、../src/ は Rust ソースファイルのため無視
  ignoreDeadLinks: [
    /\/plan\//,
    /\.\.\/src\//,
  ],

  lastUpdated: true,

  themeConfig: {
    siteTitle: 'flowsurface',

    nav: [
      { text: 'ガイド', link: '/wiki/' },
      { text: '仕様書', link: '/spec/replay' },
      { text: 'GitHub', link: 'https://github.com/flowsurface-rs/flowsurface' },
    ],

    sidebar: {
      '/wiki/': [
        {
          text: '操作ガイド',
          items: [
            { text: '概要', link: '/wiki/' },
            { text: '基本的な使い方', link: '/wiki/getting-started' },
            { text: 'チャート', link: '/wiki/charts' },
            { text: 'リプレイ', link: '/wiki/replay' },
            { text: '注文（立花証券）', link: '/wiki/orders' },
            { text: '設定・カスタマイズ', link: '/wiki/settings' },
          ],
        },
      ],
      '/spec/': [
        {
          text: '開発者向け仕様書',
          items: [
            { text: 'リプレイ機能', link: '/spec/replay' },
            { text: '立花証券 API 統合', link: '/spec/tachibana' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/flowsurface-rs/flowsurface' },
    ],

    editLink: {
      pattern: 'https://github.com/flowsurface-rs/flowsurface/edit/main/docs/:path',
      text: 'このページを編集',
    },

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
