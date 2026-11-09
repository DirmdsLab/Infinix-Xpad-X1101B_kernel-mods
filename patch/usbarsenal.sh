#!/usr/bin/env bash
# Route ConfigFS setup requests to the gadget bound to the controller.
set -euo pipefail
: "${KERNEL_SRC:?Run ./main.sh patch patch/usbarsenal.sh}"

apply_configfs_patch() {
  # Restore the unified diff's context prefix without embedding space-tab indents.
  sed 's/^|/ /' <<'PATCH' | git -C "$KERNEL_SRC" apply --whitespace=error "$@"
diff --git a/drivers/usb/gadget/configfs.c b/drivers/usb/gadget/configfs.c
--- a/drivers/usb/gadget/configfs.c
+++ b/drivers/usb/gadget/configfs.c
@@ -1546,18 +1546,19 @@
|static int android_setup(struct usb_gadget *gadget,
|			const struct usb_ctrlrequest *c)
|{
|	struct usb_composite_dev *cdev;
|	unsigned long flags;
|	struct gadget_info *gi;
|	int value = -EOPNOTSUPP;
|	struct usb_function_instance *fi;
|
-	if (!android_device)
+	cdev = get_gadget_data(gadget);
+	if (!cdev)
|		return 0;
-
-	gi = dev_get_drvdata(android_device);
+
+	gi = container_of(cdev, struct gadget_info, cdev);
|	spin_lock_irqsave(&gi->spinlock, flags);
|	cdev = get_gadget_data(gadget);
|	if (!cdev || gi->unbind) {
|		spin_unlock_irqrestore(&gi->spinlock, flags);
|		return 0;
PATCH
}

if apply_configfs_patch --reverse --check >/dev/null 2>&1; then
  echo "USB Arsenal ConfigFS patch is already applied."
  exit 0
fi

apply_configfs_patch
echo "USB_ARSENAL_CONFIGFS_APPLIED"
