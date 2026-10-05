-- ====================================================
-- B 站视频分析系统 - MySQL 表结构
-- 用法: mysql -u bili -p bili_analysis < schema.sql
-- ====================================================

USE bili_analysis;

-- 删除已存在的表 (重跑时方便)
DROP TABLE IF EXISTS comments;
DROP TABLE IF EXISTS video_scores;
DROP TABLE IF EXISTS videos;

-- ============ 1. 视频主表 ============
CREATE TABLE videos (
    bvid            VARCHAR(20)   NOT NULL PRIMARY KEY,
    aid             BIGINT        NOT NULL,
    category        VARCHAR(20)   NOT NULL,
    title           VARCHAR(255)  NOT NULL,
    description     TEXT,
    tname           VARCHAR(50),
    tid             INT,
    duration        INT,
    pubdate         DATETIME,
    owner_uid       BIGINT,
    owner_name      VARCHAR(100),
    view_count      BIGINT        DEFAULT 0,
    like_count      BIGINT        DEFAULT 0,
    coin_count      BIGINT        DEFAULT 0,
    favorite_count  BIGINT        DEFAULT 0,
    share_count     BIGINT        DEFAULT 0,
    reply_count     BIGINT        DEFAULT 0,
    danmaku_count   BIGINT        DEFAULT 0,
    INDEX idx_category (category),
    INDEX idx_pubdate (pubdate),
    INDEX idx_view (view_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============ 2. 视频评分表 ============
CREATE TABLE video_scores (
    bvid              VARCHAR(20)   NOT NULL PRIMARY KEY,
    -- 质量系数 Q 三维
    s_score           FLOAT,        -- 加权情感得分
    c_score           FLOAT,        -- 一致性
    d_score           FLOAT,        -- 讨论深度
    quality_q         FLOAT,        -- 综合质量系数
    comment_count_used INT,         -- 参与计算的评论数
    -- 评分
    score_static      FLOAT,
    score_dynamic     FLOAT,
    rank_static       INT,
    rank_dynamic      INT,
    rank_change       INT,          -- 正值=动态评分排名上升
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_quality (quality_q),
    INDEX idx_score_dynamic (score_dynamic),
    INDEX idx_rank_change (rank_change),
    CONSTRAINT fk_scores_video FOREIGN KEY (bvid) REFERENCES videos(bvid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============ 3. 评论表 ============
CREATE TABLE comments (
    rpid              BIGINT        NOT NULL PRIMARY KEY,
    bvid              VARCHAR(20)   NOT NULL,
    aid               BIGINT,
    uid               BIGINT,
    uname             VARCHAR(100),
    user_level        INT,
    ctime             DATETIME,
    like_count        INT           DEFAULT 0,
    reply_count       INT           DEFAULT 0,
    message           TEXT          NOT NULL,
    sentiment_label   VARCHAR(10),  -- positive/negative/neutral
    sentiment_score   FLOAT,        -- [-1, 1]
    confidence        FLOAT,        -- [0, 1]
    INDEX idx_bvid (bvid),
    INDEX idx_sentiment (sentiment_label),
    INDEX idx_like (like_count),
    CONSTRAINT fk_comments_video FOREIGN KEY (bvid) REFERENCES videos(bvid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 验证
SHOW TABLES;
SELECT 'Tables created successfully' AS status;
