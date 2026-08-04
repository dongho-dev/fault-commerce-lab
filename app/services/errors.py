class BusinessError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ProductNotFoundError(BusinessError):
    def __init__(self) -> None:
        super().__init__(
            code="PRODUCT_NOT_FOUND",
            message="상품을 찾을 수 없습니다.",
            status_code=404,
        )


class InsufficientStockError(BusinessError):
    def __init__(self) -> None:
        super().__init__(
            code="INSUFFICIENT_STOCK",
            message="요청한 수량만큼의 재고가 없습니다.",
            status_code=409,
        )
