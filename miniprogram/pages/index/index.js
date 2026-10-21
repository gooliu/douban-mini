// pages/index/index.js — 首页：输入用户ID查询
const api = require('../../utils/api')

Page({
  data: {
    userId: '',
    loading: false,
    user: null,
    error: '',
    // 后端设置 / 登录
    showSettings: false,
    apiBase: '',
    cookie: '',
    selfUid: '',
    apiMsg: '',
    loginMsg: '',
    testing: false,
    logining: false,
  },

  onLoad() {
    const app = getApp()
    this.setData({
      apiBase: app.globalData.apiBase,
      selfUid: app.globalData.selfUid || wx.getStorageSync('selfUid') || ''
    })
  },

  onInput(e) {
    this.setData({ userId: e.detail.value.trim(), error: '' })
  },

  /* ---------- 后端设置 ---------- */
  onToggleSettings() {
    this.setData({ showSettings: !this.data.showSettings })
  },

  onApiInput(e) {
    this.setData({ apiBase: e.detail.value, apiMsg: '' })
  },

  onCookieInput(e) {
    this.setData({ cookie: e.detail.value, loginMsg: '' })
  },

  onUidInput(e) {
    this.setData({ selfUid: e.detail.value.trim(), loginMsg: '' })
  },

  async onSaveApi() {
    const app = getApp()
    app.setApiBase(this.data.apiBase)
    this.setData({ testing: true, apiMsg: '正在连接 ' + app.globalData.apiBase + ' …' })
    try {
      const h = await api.health()
      const st = h.token_status
      const stateText = st === 'valid' ? '已登录'
                      : st === 'expired' ? '登录失效'
                      : st === 'invalid' ? '凭证无效' : '未登录'
      this.setData({
        testing: false,
        apiMsg: '✅ 连接成功（' + stateText + '）' + (h.detail ? '：' + h.detail : '')
      })
    } catch (e) {
      this.setData({
        testing: false,
        apiMsg: '❌ ' + e.message + '\n提示：真机预览要填电脑的局域网地址，' +
                '且电脑防火墙要放行 8000 端口；开发者工具需勾选「不校验合法域名」。'
      })
    }
  },

  async onLogin() {
    const cookie = (this.data.cookie || '').trim()
    if (!cookie) {
      this.setData({ loginMsg: '请先粘贴 Cookie（含 dbcl2）' })
      return
    }
    this.setData({ logining: true, loginMsg: '正在验证…' })
    try {
      const r = await api.login({ cookie, self_uid: (this.data.selfUid || '').trim() })
      if (r.ok) {
        wx.setStorageSync('selfUid', r.uid || this.data.selfUid)
        getApp().globalData.selfUid = r.uid || this.data.selfUid
        this.setData({ logining: false, loginMsg: '✅ ' + (r.detail || '登录成功') })
      } else {
        this.setData({ logining: false, loginMsg: '❌ ' + (r.detail || '登录失败') })
      }
    } catch (e) {
      this.setData({ logining: false, loginMsg: '❌ ' + e.message })
    }
  },

  async onSearch() {
    const userId = this.data.userId
    if (!userId) {
      this.setData({ error: '请输入豆瓣用户 ID 或用户名' })
      return
    }
    this.setData({ loading: true, error: '', user: null })
    try {
      // 先取用户公开信息，确认用户存在
      const res = await api.getUser(userId)
      const user = res.data
      getApp().globalData.currentUser = user
      this.setData({ user, loading: false })
      // 用户存在，直接跳到小组页
      wx.navigateTo({ url: '/pages/groups/groups' })
    } catch (e) {
      this.setData({
        loading: false,
        error: e.message.includes('404') ? '用户不存在，请检查 ID' : e.message
      })
    }
  },

  // 示例：一键填充演示 uid
  onExample() {
    this.setData({ userId: '55644233', error: '' })
  }
})
