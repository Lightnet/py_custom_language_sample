#!/usr/bin/env python3
"""
Tiny arithmetic JIT using Lark parser (no ast module at all)
pip install lark
"""

from lark import Lark, Transformer, v_args
import llvmlite.ir as ir
import llvmlite.binding as llvm
import ctypes

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()

# --------------------- Lark grammar ---------------------
grammar = """
    ?start: expr

    ?expr: expr "+" term      -> add
         | expr "-" term      -> sub
         | term

    ?term: term "*" factor    -> mul
         | term "/" factor    -> div
         | factor

    ?factor: "-" atom         -> neg
           | atom

    ?atom: NUMBER             -> number
         | "(" expr ")"

    NUMBER: /[0-9]+(\\.[0-9]+)?/

    %import common.WS
    %ignore WS
"""

parser = Lark(grammar, parser="lalr")

# --------------------- Transformer → LLVM ---------------------
class LLVMTransformer(Transformer):
    def __init__(self, builder):
        self.builder = builder

    @v_args(inline=True)
    def number(self, token):
        return ir.Constant(ir.DoubleType(), float(token))

    def add(self, items):   return self.builder.fadd(items[0], items[1])
    def sub(self, items):   return self.builder.fsub(items[0], items[1])
    def mul(self, items):   return self.builder.fmul(items[0], items[1])
    def div(self, items):   return self.builder.fdiv(items[0], items[1])
    def neg(self, items):   return self.builder.fsub(ir.Constant(ir.DoubleType(), 0.0), items[0])

    def expr(self, items):  return items[0]
    def term(self, items):  return items[0]
    def factor(self, items):return items[0]
    def atom(self, items):  return items[0]
    def start(self, items): return items[0]

# --------------------- JIT class ---------------------
class LarkJIT:
    def __init__(self):
        target = llvm.Target.from_default_triple()
        tm = target.create_target_machine()
        backing_mod = llvm.parse_assembly("")
        self.engine = llvm.create_mcjit_compiler(backing_mod, tm)

    def compile(self, source: str) -> float:
        tree = parser.parse(source)

        module = ir.Module()
        func_ty = ir.FunctionType(ir.DoubleType(), [])
        func = ir.Function(module, func_ty, "jit_expr")
        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        transformer = LLVMTransformer(builder)
        result = transformer.transform(tree)
        builder.ret(result)

        llvm_mod = llvm.parse_assembly(str(module))
        llvm_mod.verify()
        self.engine.add_module(llvm_mod)
        self.engine.finalize_object()

        addr = self.engine.get_function_address("jit_expr")
        cfunc = ctypes.CFUNCTYPE(ctypes.c_double)(addr)
        return cfunc()


# --------------------- Test ---------------------
if __name__ == "__main__":
    jit = LarkJIT()

    tests = [
        "1.0 + 2.0",
        "10 - 4.5",
        "2.5 * 8",
        "15.0 / 3",
        "(2 + 5) * 7 - 3",
        "-10.5 + 15.5",
        "-(3 + 4) * 2",
        "2.5 * (3 + 4.5) * 2",
    ]

    for expr in tests:
        result = jit.compile(expr)
        print(f"{expr:35} => {result:.10g}")