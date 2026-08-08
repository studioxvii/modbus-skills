# Byte and Word Layouts

For two 16-bit registers, name source bytes `A B C D` in register and byte arrival order.

| Layout | Decode order |
|---|---|
| `ABCD` | `A B C D` |
| `BADC` | `B A D C` |
| `CDAB` | `C D A B` |
| `DCBA` | `D C B A` |

Do not use labels such as “little endian” without also recording the explicit permutation.

For 64-bit values, store and report the full eight-letter permutation. Evaluate only declared transformations. Do not guess an undocumented layout.

Apply scaling after integer or IEEE floating-point decoding.
