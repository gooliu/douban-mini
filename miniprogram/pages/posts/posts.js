// pages/posts/posts.js — 发言记录页
// 两种模式：
//   1. 查看某用户的发言记录（无参数，读 globalData.currentUser）
//   2. 查看某小组的近期话题（groupId + groupName 参数，真实数据）
const api = require('../../utils/api')

Page({
  data: {
    mode: 'user',          // user=用户发言记录 / group=小组话题
    user: null,
    groupId: '',
    groupName: '',
    topics: [],
    total: 0,
    source: '',
    message: '',
    loading: true,
    error: ''
  },

  onLoad(options) {
    if (options.groupId) {
      // 小组话题模式（真实数据）
      this.setData({
        mode: 'group',
        groupId: options.groupId,
        groupName: decodeURIComponent(options.groupName || '小组话题')
      })
      wx.setNavigationBarTitle({ title: this.data.groupName })
      this.fetchGroupTopics()
    } else {
      // 用户发言记录模式
      const user = getApp().globalData.currentUser
      if (!user) {
        this.setData({ loading: false, error: '缺少用户信息，请返回首页查询' })
        return
      }
      this.setData({ user })
      wx.setNavigationBarTitle({ title: user.name + ' 的发言' })
      this.fetchUserTopics()
    }
  },

  onPullDownRefresh() {
    const p = this.data.mode === 'group' ? this.fetchGroupTopics() : this.fetchUserTopics()
    p.then(() => wx.stopPullDownRefresh())
  },

  async fetchUserTopics() {
    this.setData({ loading: true, error: '' })
    try {
      const res = await api.getUserTopics(this.data.user.id)
      const d = res.data || {}
      this.setData({
        topics: d.topics || [],
        total: d.total || (d.topics || []).length,
        source: res.source,
        message: res.message || '',
        loading: false
      })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  async fetchGroupTopics() {
    this.setData({ loading: true, error: '' })
    try {
      const res = await api.getGroupTopics(this.data.groupId, 20)
      const d = res.data || {}
      this.setData({
        topics: d.topics || [],
        total: d.total || (d.topics || []).length,
        source: 'douban',
        loading: false
      })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  // 打开豆瓣原帖（复制链接）
  async onCopyLink(e) {
    const url = e.currentTarget.dataset.url
    if (!url) return
    wx.setClipboardData({ data: url })
  }
})
