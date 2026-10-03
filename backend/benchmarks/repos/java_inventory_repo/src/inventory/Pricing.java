/**
 * 按采购数量返回折扣档位（百分比）。
 */
public class Pricing {
    /**
     * >=100 返 30%，>=50 返 20%，否则 0。
     */
    public static int discountTier(int quantity) {
        // BUG: 边界应使用 >=，这里用 > 导致恰好 100/50 落入更低档位。
        if (quantity > 100) {
            return 30;
        }
        if (quantity > 50) {
            return 20;
        }
        return 0;
    }
}
