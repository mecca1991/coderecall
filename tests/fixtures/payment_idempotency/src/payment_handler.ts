import { payments } from "./payment_repository";
import { capturePayment } from "./payment_service";

export async function handlePayment(request: PaymentRequest) {
  const existing = await payments.findByIdempotencyKey(request.idempotencyKey);
  if (existing) {
    return existing;
  }
  return capturePayment(request);
}
