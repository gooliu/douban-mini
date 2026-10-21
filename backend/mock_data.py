# -*- coding: utf-8 -*-
"""演示数据（未配置 DOUBAN_TOKEN 时的兜底数据源）

接口返回会带 "source": "mock" 字段，前端会明确提示数据为演示数据。
配置 DOUBAN_TOKEN 后自动切换为真实数据。
"""
import time

# 以真实存在的豆瓣用户/小组信息为蓝本构造
MOCK_USER = {
    "id": "55644233",
    "uid": "55644233",
    "name": "豆友55644233",
    "avatar": "https://img3.doubanio.com/img/files/file-1609845567.png",
    "signature": "慢一点，生活会等你",
    "loc": "上海",
    "reg_time": "2017-03-12",
    "followers_count": 128,
    "following_count": 96,
}

MOCK_JOINED_GROUPS = {
    "count": 6,
    "total": 6,
    "groups": [
        {
            "id": "146409",
            "name": "上海租房",
            "member_count": 255294,
            "create_time": "2008-11-03 14:27:49",
            "desc": "欢迎来到【上海租房】小组！这里是沪漂租房人的专属乐园。",
            "category": "信息",
            "url": "https://www.douban.com/group/shanghaizufang/",
        },
        {
            "id": "577929",
            "name": "不吃香菜联盟",
            "member_count": 398761,
            "create_time": "2015-06-20",
            "desc": "世界这么大，为什么香菜要为难我们。",
            "category": "生活",
            "url": "https://www.douban.com/group/no_cilantro/",
        },
        {
            "id": "59533",
            "name": "加班都是骗人的",
            "member_count": 142057,
            "create_time": "2011-09-15",
            "desc": "拒绝无意义加班，从摸鱼做起。",
            "category": "职场",
            "url": "https://www.douban.com/group/no_ot/",
        },
        {
            "id": "susan",
            "name": "苏珊的读书会",
            "member_count": 8823,
            "create_time": "2013-04-02",
            "desc": "每月一本书，认真读完它。",
            "category": "文化",
            "url": "https://www.douban.com/group/susan/",
        },
        {
            "id": "runners",
            "name": "跑步爱好者",
            "member_count": 210945,
            "create_time": "2010-12-01",
            "desc": "以跑半马为目标，互相监督打卡。",
            "category": "运动",
            "url": "https://www.douban.com/group/runners/",
        },
        {
            "id": "334967",
            "name": "今天你抑郁了吗",
            "member_count": 187650,
            "create_time": "2014-08-11",
            "desc": "说出来，会好一点。",
            "category": "情感",
            "url": "https://www.douban.com/group/334967/",
        },
    ],
}


def _ts(days_ago: int) -> str:
    t = time.time() - days_ago * 86400
    st = time.localtime(t)
    return time.strftime("%Y-%m-%d %H:%M:%S", st)


MOCK_TOPICS = [
    {
        "topic_id": "291234567",
        "title": "静安寺附近一室户转租，8月中旬可入住",
        "abstract": "距离地铁2号线步行5分钟，民水民电，房东人很好，因工作变动转租…",
        "create_time": _ts(3),
        "group_id": "146409",
        "group_name": "上海租房",
        "comments_count": 23,
        "likes_count": 8,
    },
    {
        "topic_id": "291238901",
        "title": "以跑半马为目标，应该练距离还是练心率？",
        "abstract": "目前5km配速6'30''，想三个月内完成半马，求大佬指点训练计划…",
        "create_time": _ts(11),
        "group_id": "runners",
        "group_name": "跑步爱好者",
        "comments_count": 41,
        "likes_count": 36,
    },
    {
        "topic_id": "291240222",
        "title": "饭店能不能把香菜改成可选啊！！",
        "abstract": "点了个牛肉面，备注三遍不要香菜，结果端上来还是满满的香菜…",
        "create_time": _ts(25),
        "group_id": "577929",
        "group_name": "不吃香菜联盟",
        "comments_count": 156,
        "likes_count": 289,
    },
    {
        "topic_id": "291241777",
        "title": "《百年孤独》读完打卡，聊聊你最喜欢的角色",
        "abstract": "第三代阿尔卡蒂奥的死让我愣了很久，命运轮回感太强了…",
        "create_time": _ts(47),
        "group_id": "susan",
        "group_name": "苏珊的读书会",
        "comments_count": 67,
        "likes_count": 102,
    },
    {
        "topic_id": "291243555",
        "title": "试用期最后一天被通知不合适，怎么办",
        "abstract": "入职两个月零29天，今天领导找我谈话说不合适，连个具体原因都没有…",
        "create_time": _ts(80),
        "group_id": "59533",
        "group_name": "加班都是骗人的",
        "comments_count": 214,
        "likes_count": 356,
    },
]

MOCK_COMMENTS = [
    {
        "topic_id": "291250001",
        "title": "上海租房避坑指南（长文）",
        "comment": "mark，正好下个月要换房，感谢楼主整理！",
        "create_time": _ts(5),
        "group_id": "146409",
        "group_name": "上海租房",
    },
    {
        "topic_id": "291260002",
        "title": "你们跑步都听什么歌？",
        "comment": "强推《Stronger》，节奏踩点神器",
        "create_time": _ts(18),
        "group_id": "runners",
        "group_name": "跑步爱好者",
    },
    {
        "topic_id": "291270003",
        "title": "今天你跑步了吗 第1024期",
        "comment": "打卡，5.2km done",
        "create_time": _ts(33),
        "group_id": "runners",
        "group_name": "跑步爱好者",
    },
]
