import { database } from "./database";
import { processor } from "./processor";

export async function capturePayment(input: PaymentInput) {
  return database.transaction(async () => {
    const charge = await processor.charge(input.amount);
    await database.payments.insert({
      paymentId: charge.id,
      idempotencyKey: input.idempotencyKey,
    });
    return charge;
  });
}
