/**
 * 库存记录，判断是否能满足请求数量。
 */
public class Inventory {
    private final int stock;

    public Inventory(int stock) {
        this.stock = stock;
    }

    /**
     * 请求数量不超过当前库存即可满足。
     */
    public boolean canFulfill(int requested) {
        // BUG: 应该用 <= 允许恰好等于库存，这里用 < 导致边界数量被拒。
        return requested < stock;
    }
}
