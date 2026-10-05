/**
 * Inventory 与 Pricing 的独立验收测试。
 *
 * 通过 JEP 458 多文件源码启动：`java src/inventory/InventoryTest.java`
 * 会自动编译并运行同目录树中被引用的其它类。
 */
public class InventoryTest {
    private static int failures = 0;

    private static void check(String name, boolean condition) {
        if (condition) {
            System.out.println("PASS " + name);
        } else {
            System.out.println("FAIL " + name);
            failures++;
        }
    }

    public static void main(String[] args) {
        Inventory inventory = new Inventory(5);
        check("canFulfill exact stock", inventory.canFulfill(5));
        check("canFulfill below stock", inventory.canFulfill(3));
        check("cannot fulfill above stock", !inventory.canFulfill(6));

        check("discount tier at exactly 100", Pricing.discountTier(100) == 30);
        check("discount tier at exactly 50", Pricing.discountTier(50) == 20);
        check("discount tier above 100", Pricing.discountTier(101) == 30);
        check("no discount below 50", Pricing.discountTier(49) == 0);

        if (failures > 0) {
            System.out.println(failures + " test(s) failed");
            System.exit(1);
        }
        System.out.println("All tests passed");
    }
}
