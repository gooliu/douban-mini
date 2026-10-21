// pages/groups/groups.js — 用户加入的小组列表
const api = require('../../utils/api')

Page({
  data: {
    user: null,
    groups: [],
    total: 0,
    source: '',
    message: '',
    loading: true,
    error: ''
  },

  onLoad() {
    const user = getApp().globalData.currentUser
    this.setData({ user })
    if (!user) {
      this.setData({ loading: false, error: '缺少用户信息，请返回首页查询' })
      return
    }
    wx.setNavigationBarTitle({ title: user.name + ' 的小组' })
    this.fetch()
  },

  onPullDownRefresh() {
    this.fetch().then(() => wx.stopPullDownRefresh())
  },

  async fetch() {
    this.setData({ loading: true, error: '' })
    try {
      const res = await api.getUserGroups(this.data.user.id)
      const d = res.data || {}
      this.setData({
        groups: d.groups || [],
        total: d.total || (d.groups || []).length,
        source: res.source,
        message: res.message || '',
        loading: false
      })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  // 跳转到该用户的发言记录
  goPosts() {
    wx.navigateTo({ url: '/pages/posts/posts' })
  },

  // 查看小组近期话题（真实数据）
  async goGroupTopics(e) {
    const g = e.currentTarget.dataset.group
    wx.navigateTo({
      url: `/pages/posts/posts?groupId=${g.id}&groupName=${encodeURIComponent(g.name)}`
    })
  }
})
