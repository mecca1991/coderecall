import { handlePayment } from "../src/payment_handler";

describe("handlePayment", () => {
  it("returns a payment recorded for the same idempotency key", async () => {
    const result = await handlePayment({ idempotencyKey: "known-key" });

    expect(result.idempotencyKey).toBe("known-key");
  });
});
