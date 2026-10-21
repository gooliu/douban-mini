// utils/api.js — 后端请求封装
const app = getApp()

/**
 * 通用请求
 * @param {string} path 接口路径，如 /api/user/123
 */
function request(path) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: app.globalData.apiBase + path,
      method: 'GET',
      success(res) {
        if (res.statusCode === 200 && res.data.ok) {
          resolve(res.data)
        } else {
          const detail = (res.data && (res.data.detail || res.data.message)) || ('HTTP ' + res.statusCode)
          reject(new Error(detail))
        }
      },
      fail(err) {
        reject(new Error('网络错误：' + (err.errMsg || '无法连接后端')))
      }
    })
  })
}

/** 用户公开信息（真实数据） */
function getUser(userId) {
  return request('/api/user/' + encodeURIComponent(userId))
}

/** 用户加入的小组 */
function getUserGroups(userId) {
  return request(`/api/user/${encodeURIComponent(userId)}/groups`)
}

/** 用户发言记录 */
function getUserTopics(userId) {
  return request(`/api/user/${encodeURIComponent(userId)}/topics`)
}

/** 小组近期话题（真实数据） */
function getGroupTopics(groupId, count) {
  return request(`/api/group/${encodeURIComponent(groupId)}/topics?count=${count || 20}`)
}

/** 服务状态 + 登录是否有效：{token_status, self_uid, detail} */
function health() {
  return request('/api/health')
}

/** POST 请求（提交登录凭证用） */
function post(path, body) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: app.globalData.apiBase + path,
      method: 'POST',
      header: { 'Content-Type': 'application/json' },
      data: body || {},
      success(res) {
        if (res.statusCode === 200) {
          resolve(res.data)
        } else {
          reject(new Error(((res.data || {}).detail) || ('HTTP ' + res.statusCode)))
        }
      },
      fail(err) {
        reject(new Error('网络错误：' + (err.errMsg || '无法连接后端')))
      }
    })
  })
}

/** 登录：{cookie, self_uid} 或 {token}，返回 {ok, status, uid, name, detail} */
function login(payload) {
  return post('/api/config/token', payload)
}

/** 退出登录（清除服务端保存的凭证，把工具给别人用之前调用） */
function logout() {
  return post('/api/config/logout', {})
}

module.exports = { request, post, health, login, logout,
                   getUser, getUserGroups, getUserTopics, getGroupTopics }
