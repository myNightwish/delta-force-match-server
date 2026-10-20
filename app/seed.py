"""演示数据：4 个玩家（含真实截图模板字段）+ 2 个开放房间。幂等。"""
from app.db.session import SessionLocal, init_db
from app.models import MatchPost, PlayerProfile, User

TEMPLATE = dict(
    mode="烽火地带",
    tier_name="钻石II", tier_stars=4, tier_score=4418,
    matches=1673, play_hours=332.0,
    radar_combat=59, radar_survival=77, radar_coop=56, radar_search=64, radar_wealth=91,
    profit_ratio="1.1M", extract_value="1.1B",
    kda_k=1.0, kda_d=1.5, kda_a=0.9, kills=1258,
    hit_rate=34.0, precise_kill_rate=63.3, extract_rate=40.4,
    teammate_extract_value="22.7M", rescues=160, revives=114,
    season="S1", ocr_confidence=0.97,
)

PLAYERS = [
    ("13800000001", "烽火老兵", "tech", dict(TEMPLATE, game_name="烽火老兵")),
    ("13800000002", "夜行舔包人", "tech", dict(TEMPLATE, tier_name="铂金", tier_stars=2,
        tier_score=3200, kda_d=1.2, extract_rate=35.0, matches=800, game_name="夜行舔包人")),
    ("13800000003", "椰子味奶茶", "companion", dict(TEMPLATE, tier_name="铂金", tier_stars=1,
        tier_score=3100, kda_d=1.1, extract_rate=42.0, matches=950, game_name="椰子味奶茶")),
    ("13800000004", "摸鱼队长", "companion", dict(TEMPLATE, tier_name="黄金", tier_stars=3,
        tier_score=2200, kda_d=0.9, extract_rate=38.5, matches=500, game_name="摸鱼队长")),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        for phone, nickname, faction, fields in PLAYERS:
            user = db.query(User).filter(User.phone == phone).first()
            if not user:
                user = User(phone=phone, nickname=nickname, faction=faction)
                db.add(user)
                db.flush()
                db.add(PlayerProfile(user_id=user.id, **fields))
        db.commit()

        if db.query(MatchPost).count() == 0:
            owner = db.query(User).filter(User.phone == "13800000001").first()
            owner2 = db.query(User).filter(User.phone == "13800000003").first()
            db.add(MatchPost(owner_id=owner.id, island="tech", recruit_count=1,
                             kd_min=1.2, mic_required=True, play_time="今晚 20:00"))
            db.add(MatchPost(owner_id=owner2.id, island="companion", recruit_count=2,
                             mic_required=False, play_time="周末下午"))
            db.commit()
        print("seed ok")
    finally:
        db.close()


if __name__ == "__main__":
    main()
