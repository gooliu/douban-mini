// app.js
App({
  onLaunch() {
    // 后端地址：优先用上次设置的（真机调试时填局域网地址，如 http://192.168.1.5:8000）
    try {
      const saved = wx.getStorageSync('apiBase')
      if (saved) this.globalData.apiBase = saved
      const uid = wx.getStorageSync('selfUid')
      if (uid) this.globalData.selfUid = uid
    } catch (e) {
      // 忽略读取失败
    }
  },
  /** 设置并持久化后端地址 */
  setApiBase(url) {
    let u = (url || '').trim().replace(/\/+$/, '')
    if (!u) return
    if (!/^https?:\/\//i.test(u)) u = 'http://' + u
    this.globalData.apiBase = u
    wx.setStorageSync('apiBase', u)
  },
  globalData: {
    // 后端地址：本机调试用 127.0.0.1，真机/部署后在首页「后端设置」里改
    apiBase: 'http://127.0.0.1:8000',
    selfUid: '',
    // 当前查询的用户
    currentUser: null
  }
})
