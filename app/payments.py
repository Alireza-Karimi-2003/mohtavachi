from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings
from .db import PaymentOrder


class PaymentError(Exception):
    """Expected payment/provider error safe to show to the user."""


@dataclass(frozen=True)
class PaymentRequestResult:
    authority: str
    payment_url: str


@dataclass(frozen=True)
class PaymentVerifyResult:
    code: int
    ref_id: str | None
    message: str


def _parse_response_data(body: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    if not isinstance(body, dict):
        raise PaymentError("پاسخ نامعتبر از درگاه پرداخت دریافت شد.")
    raw_data = body.get("data")
    data = raw_data if raw_data is not None else {}
    if not isinstance(data, dict):
        raise PaymentError("پاسخ نامعتبر از درگاه پرداخت دریافت شد.")
    return data, body.get("errors")


def _parse_code(value: Any) -> int:
    if isinstance(value, bool):
        raise PaymentError("کد پاسخ درگاه نامعتبر است.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PaymentError("کد پاسخ درگاه نامعتبر است.") from exc


class ZarinPalClient:
    """Small async adapter for ZarinPal REST API v4."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._external_client = http_client

    @property
    def api_base_url(self) -> str:
        return "https://sandbox.zarinpal.com" if settings.zarinpal_sandbox else "https://api.zarinpal.com"

    @property
    def gateway_base_url(self) -> str:
        return "https://sandbox.zarinpal.com" if settings.zarinpal_sandbox else "https://www.zarinpal.com"

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not settings.zarinpal_merchant_id:
            raise PaymentError("کد پذیرنده زرین‌پال تنظیم نشده است.")

        client = self._external_client
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=httpx.Timeout(settings.payment_timeout, connect=10.0))
        try:
            response = await client.post(
                f"{self.api_base_url}{path}",
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Mavazegar/1.0",
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise PaymentError("ارتباط با درگاه پرداخت برقرار نشد.") from exc
        except ValueError as exc:
            raise PaymentError("پاسخ نامعتبر از درگاه پرداخت دریافت شد.") from exc
        finally:
            if owns_client:
                await client.aclose()

        if not isinstance(body, dict):
            raise PaymentError("پاسخ نامعتبر از درگاه پرداخت دریافت شد.")
        return body

    async def request_payment(
        self,
        *,
        amount_rial: int,
        description: str,
        callback_url: str,
        mobile: str | None = None,
        email: str | None = None,
    ) -> PaymentRequestResult:
        if amount_rial <= 0:
            raise PaymentError("مبلغ پرداخت نامعتبر است.")
        if not callback_url:
            raise PaymentError("آدرس callback پرداخت تنظیم نشده است.")

        metadata: dict[str, str] = {}
        if mobile:
            metadata["mobile"] = mobile
        if email:
            metadata["email"] = email

        payload: dict[str, Any] = {
            "merchant_id": settings.zarinpal_merchant_id,
            "amount": amount_rial,
            "description": description[:255],
            "callback_url": callback_url,
        }
        if metadata:
            payload["metadata"] = metadata

        body = await self._post("/pg/v4/payment/request.json", payload)
        data, errors = _parse_response_data(body)
        code = _parse_code(data.get("code")) if "code" in data else 0
        authority = str(data.get("authority") or "").strip()

        if errors or code != 100 or not authority or len(authority) > 255:
            message = "خطا در ایجاد درخواست پرداخت."
            if isinstance(errors, dict):
                message = str(errors.get("message") or message)
            elif isinstance(data.get("message"), str):
                message = str(data.get("message") or message)
            raise PaymentError(message[:1000])

        return PaymentRequestResult(
            authority=str(authority),
            payment_url=f"{self.gateway_base_url}/pg/StartPay/{authority}",
        )

    async def verify_payment(self, *, amount_rial: int, authority: str) -> PaymentVerifyResult:
        if amount_rial <= 0 or not authority:
            raise PaymentError("اطلاعات تراکنش برای verification ناقص است.")

        body = await self._post(
            "/pg/v4/payment/verify.json",
            {
                "merchant_id": settings.zarinpal_merchant_id,
                "amount": amount_rial,
                "authority": authority,
            },
        )
        data, errors = _parse_response_data(body)
        code = _parse_code(data.get("code")) if "code" in data else 0
        message = str(data.get("message") or "")
        if errors:
            if not message and isinstance(errors, dict):
                message = str(errors.get("message") or "")
            raise PaymentError((message or "پاسخ خطادار از درگاه پرداخت دریافت شد.")[:1000])
        ref_id = data.get("ref_id")
        if ref_id is not None:
            ref_id = str(ref_id).strip()
            if not ref_id or len(ref_id) > 255:
                raise PaymentError("شناسه رهگیری درگاه نامعتبر است.")
        return PaymentVerifyResult(code=code, ref_id=ref_id, message=message[:1000])


def payment_url_from_authority(authority: str) -> str:
    authority = str(authority or "").strip()
    if not authority or len(authority) > 255:
        raise PaymentError("شناسه پرداخت نامعتبر است.")
    base = "https://sandbox.zarinpal.com" if settings.zarinpal_sandbox else "https://www.zarinpal.com"
    return f"{base}/pg/StartPay/{authority}"


def payment_callback_url() -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/payment/callback" if base else ""


def validate_payment_configuration() -> None:
    if not settings.payment_enabled:
        raise PaymentError("پرداخت هنوز فعال نشده است.")
    if not settings.zarinpal_merchant_id:
        raise PaymentError("کد پذیرنده زرین‌پال تنظیم نشده است.")
    if str(settings.app_env).strip().lower() == "production" and settings.zarinpal_sandbox:
        raise PaymentError("تنظیمات درگاه برای Production امن نیست؛ Sandbox نباید فعال باشد.")
    callback = payment_callback_url()
    if not callback:
        raise PaymentError("public_base_url برای callback پرداخت تنظیم نشده است.")
    if not callback.lower().startswith("https://"):
        raise PaymentError("callback پرداخت باید با HTTPS باشد.")


async def create_checkout(
    *,
    order: PaymentOrder,
    mobile: str | None = None,
    email: str | None = None,
    client: ZarinPalClient | None = None,
) -> PaymentRequestResult:
    validate_payment_configuration()
    gateway = client or ZarinPalClient()
    return await gateway.request_payment(
        amount_rial=order.amount_rial,
        description=f"محتواچی - پلن {order.plan} - سفارش #{order.id}",
        callback_url=payment_callback_url(),
        mobile=mobile,
        email=email,
    )
