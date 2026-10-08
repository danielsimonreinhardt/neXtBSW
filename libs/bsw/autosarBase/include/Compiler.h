/* AUTOSAR Compiler Abstraction (AUTOSAR_SWS_CompilerAbstraction) for GCC / Clang.
 * Memory classes are ignored: a flat memory model without near/far pointers. */

#ifndef COMPILER_H
#define COMPILER_H

#define AUTOMATIC
#define TYPEDEF
#define STATIC static
#define INLINE inline
#define LOCAL_INLINE static inline

#ifndef NULL_PTR
#define NULL_PTR ((void*)0)
#endif

#define FUNC(rettype, memclass) rettype
#define FUNC_P2CONST(rettype, ptrclass, memclass) const rettype*
#define FUNC_P2VAR(rettype, ptrclass, memclass) rettype*
#define P2VAR(ptrtype, memclass, ptrclass) ptrtype*
#define P2CONST(ptrtype, memclass, ptrclass) const ptrtype*
#define CONSTP2VAR(ptrtype, memclass, ptrclass) ptrtype* const
#define CONSTP2CONST(ptrtype, memclass, ptrclass) const ptrtype* const
#define P2FUNC(rettype, ptrclass, fctname) rettype (*fctname)
#define CONSTP2FUNC(rettype, ptrclass, fctname) rettype (* const fctname)
#define CONST(consttype, memclass) const consttype
#define VAR(vartype, memclass) vartype

#endif /* COMPILER_H */
