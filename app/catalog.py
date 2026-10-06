"""Demo storefront catalog that can be inserted idempotently through the normal repositories."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Product
from app.repositories.inventory import InventoryRepository
from app.repositories.product import ProductRepository

IMAGE_ROOT = "/static/assets/products"


@dataclass(frozen=True)
class CatalogItem:
    name: str
    brand: str
    category: str
    unit_price: int
    list_price: int | None
    stock: int
    image: str
    description: str


CATALOG: tuple[CatalogItem, ...] = (
    CatalogItem("무선 기계식 키보드 87키 갈축", "키라인", "digital", 89_000, 119_000, 34,
                "keyboard",
                "블루투스 3대 멀티페어링과 유선 겸용. 핫스왑 소켓으로 스위치 교체가 쉽습니다."),
    CatalogItem("노이즈캔슬링 블루투스 헤드폰", "사운드랩", "digital", 159_000, 219_000, 12,
                "headphones",
                "하이브리드 ANC와 최대 40시간 재생. 접이식 구조로 휴대가 편합니다."),
    CatalogItem("27인치 QHD IPS 모니터 165Hz", "뷰포인트", "digital", 289_000, 349_000, 8,
                "monitor",
                "2560×1440 해상도와 1ms 응답속도. 높이·틸트 조절 스탠드 포함."),
    CatalogItem("저소음 무선 마우스", "키라인", "digital", 24_900, 32_000, 120,
                "mouse",
                "클릭음을 줄인 사무용 무선 마우스. USB 수신기와 블루투스를 모두 지원합니다."),
    CatalogItem("고속충전 보조배터리 20000mAh", "볼트온", "digital", 32_900, 45_000, 0,
                "powerbank",
                "PD 45W 고속충전과 잔량 디스플레이. 기내 반입 가능한 용량입니다."),
    CatalogItem("호텔식 사계절 차렵이불 퀸", "포근살림", "home", 69_000, 99_000, 25,
                "blanket",
                "60수 면 커버와 마이크로화이바 충전재. 세탁기 사용이 가능합니다."),
    CatalogItem("무드등 LED 터치 스탠드", "라이트웨이", "home", 39_800, None, 3,
                "lamp",
                "3단계 색온도와 무단 밝기 조절. 침대 옆이나 책상 위에 두기 좋은 크기입니다."),
    CatalogItem("접이식 패브릭 수납박스 3개입", "정리공간", "home", 19_900, 27_000, 80,
                "storagebox",
                "옷장과 선반에 맞는 표준 규격. 쓰지 않을 때는 납작하게 접어 보관합니다."),
    CatalogItem("극세사 호텔 수건 10장", "포근살림", "home", 22_900, None, 150,
                "towel",
                "흡수력이 좋은 극세사 원단. 40×80cm 넉넉한 크기입니다."),
    CatalogItem("스테인리스 진공 텀블러 590ml", "데일리컵", "kitchen", 18_900, 25_000, 64,
                "tumbler",
                "이중 진공 구조로 보온·보냉 유지. 슬라이드 뚜껑으로 한 손 사용이 가능합니다."),
    CatalogItem("핸드드립 커피 입문 세트", "브루하우스", "kitchen", 45_000, 58_000, 18,
                "dripset",
                "드리퍼, 서버, 드립포트, 필터 40매 구성. 처음 시작하기 좋은 기본 세트입니다."),
    CatalogItem("논스틱 IH 프라이팬 28cm", "쿡웰", "kitchen", 34_900, 49_000, 40,
                "pan",
                "5중 코팅과 인덕션 호환 바닥. 손잡이가 오래 들어도 가볍습니다."),
    CatalogItem("무선 전기포트 1.7L", "쿡웰", "kitchen", 29_800, None, 2,
                "kettle",
                "스테인리스 내부와 자동 전원 차단. 360도 회전 받침을 사용합니다."),
    CatalogItem("제주 노지 감귤 3kg", "산지직송", "food", 17_900, 22_000, 60,
                "tangerine",
                "제주 농가에서 수확 후 바로 보내는 노지 감귤. 크기는 섞여서 발송됩니다."),
    CatalogItem("무항생제 신선란 30구", "아침농장", "food", 9_980, None, 200,
                "eggs",
                "무항생제 인증 농장의 신선란. 산란일 기준 3일 이내 출고합니다."),
    CatalogItem("콜드브루 커피 원액 1L", "브루하우스", "food", 12_900, 16_000, 45,
                "coldbrew",
                "물이나 우유에 1:4로 희석해 마시는 원액. 약 20잔 분량입니다."),
    CatalogItem("오트 그래놀라 1kg", "아침농장", "food", 14_500, 19_000, 5,
                "granola",
                "통귀리와 견과류, 건과일을 오븐에 구운 그래놀라. 지퍼백 포장입니다."),
    CatalogItem("수분 진정 토너 300ml", "클리어데이", "beauty", 21_000, 28_000, 70,
                "toner",
                "약산성 포뮬러로 세안 후 피부를 정돈합니다. 대용량 펌프형입니다."),
    CatalogItem("저자극 무기자차 선크림 SPF50+", "클리어데이", "beauty", 16_900, 24_000, 90,
                "suncream",
                "백탁이 적은 무기자차 선크림. 민감한 피부도 쓰기 좋은 제형입니다."),
    CatalogItem("핸드크림 3종 선물세트", "포레스트노트", "beauty", 13_900, None, 4,
                "handcream",
                "우디, 플로럴, 시트러스 향 50ml 3종. 선물 포장 상자에 담아 보냅니다."),
    CatalogItem("TPE 요가매트 10mm", "무브핏", "sports", 25_900, 35_000, 50,
                "yogamat",
                "양면 미끄럼 방지 패턴과 10mm 쿠션. 휴대용 스트랩을 함께 드립니다."),
    CatalogItem("소프트 덤벨 5kg 2개 세트", "무브핏", "sports", 39_900, None, 15,
                "dumbbell",
                "바닥 긁힘을 줄이는 우레탄 코팅 덤벨. 홈트레이닝 기본 구성입니다."),
    CatalogItem("경량 쿠셔닝 러닝화", "스트라이드", "sports", 79_000, 109_000, 22,
                "runningshoe",
                "230g대 경량 설계와 반발력 좋은 미드솔. 데일리 러닝용입니다."),
    CatalogItem("캠핑 접이식 릴렉스 체어", "아웃도어랩", "sports", 42_000, 56_000, 9,
                "campchair",
                "알루미늄 프레임으로 가볍고 최대 120kg까지 지지합니다. 수납 가방 포함."),
)


def seed_catalog(session: Session) -> list[str]:
    """Insert catalog products whose names do not exist yet. Returns the inserted names."""
    products = ProductRepository(session)
    inventories = InventoryRepository(session)
    inserted: list[str] = []
    with session.begin():
        existing = set(session.scalars(select(Product.name)).all())
        for item in CATALOG:
            if item.name in existing:
                continue
            product = products.create(
                name=item.name,
                unit_price=item.unit_price,
                category=item.category,
                brand=item.brand,
                description=item.description,
                list_price=item.list_price,
                image_url=f"{IMAGE_ROOT}/{item.image}.jpg",
            )
            inventories.create(product_id=product.id, initial_stock=item.stock)
            inserted.append(item.name)
    return inserted
