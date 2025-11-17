#!/usr/bin/env python3
"""
TinyJIT – fully working with:
  • let
  • print("text") and print(value)
  • if (…) { … } else { … }
  • all comparisons: > < >= <= == !=
  • arithmetic, variables, parentheses
"""

import llvmlite.ir as ir
import llvmlite.binding as llvm
import ctypes
from typing import Dict

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()


class TinyJIT:
    def __init__(self):
        target = llvm.Target.from_default_triple()
        self.target_machine = target.create_target_machine()
        self.module = ir.Module()
        self.builder = None
        self.printf = None
        self.variables: Dict[str, ir.AllocaInstr] = {}
        self._str_counter = 0

    def compile_and_run(self, source: str) -> None:
        self.variables.clear()
        self._str_counter = 0

        func_ty = ir.FunctionType(ir.VoidType(), [])
        func = ir.Function(self.module, func_ty, name="main")
        entry = func.append_basic_block("entry")
        self.builder = ir.IRBuilder(entry)

        self.printf = self._declare_printf()
        self._parse_and_execute(source)
        self.builder.ret_void()

        llvm_mod = llvm.parse_assembly(str(self.module))
        llvm_mod.verify()

        ee = llvm.create_mcjit_compiler(llvm_mod, self.target_machine)
        ee.finalize_object()
        ee.run_static_constructors()

        addr = ee.get_function_address("main")
        cfunc = ctypes.CFUNCTYPE(None)(addr)
        cfunc()

    def _declare_printf(self):
        void_ptr = ir.IntType(8).as_pointer()
        return ir.Function(self.module,
                           ir.FunctionType(ir.IntType(32), [void_ptr], var_arg=True),
                           name="printf")

    # ------------------------------------------------------------------ parsing
    def _parse_and_execute(self, source: str):
        lines = [ln.rstrip() for ln in source.splitlines() if ln.strip()]
        i = 0
        while i < len(lines):
            line = lines[i].rstrip(";").strip()

            if line.startswith("if("):
                i = self._parse_if(lines, i)
                continue

            if line.startswith("let "):
                self._parse_let(line[4:])
            elif line.startswith("print("):
                inside = line[6:-1].strip()
                if inside.startswith('"') and inside.endswith('"'):
                    self._print_string(inside[1:-1])
                else:
                    self._print_value(self._eval_expr(inside))
            elif line.startswith("print "):
                arg = line[6:].strip()
                if arg.startswith('"') and arg.endswith('"'):
                    self._print_string(arg[1:-1])
                else:
                    self._print_value(self._eval_expr(arg))
            else:
                self._eval_expr(line)

            i += 1

    def _parse_if(self, lines: list[str], start_idx: int) -> int:
        line = lines[start_idx].rstrip(";").strip()
        if not line.startswith("if("):
            raise SyntaxError("Expected 'if('")

        cond_end = line.find("){")
        if cond_end == -1:
            raise SyntaxError("Expected '){'")

        cond_str = line[3:cond_end].strip()
        cond_val = self._eval_condition(cond_str)

        then_block = self.builder.append_basic_block("if.then")
        else_block = self.builder.append_basic_block("if.else")
        cont_block = self.builder.append_basic_block("if.cont")

        self.builder.cbranch(cond_val, then_block, else_block)

        # === THEN block ===
        self.builder.position_at_end(then_block)
        i = start_idx + 1
        while i < len(lines):
            line = lines[i]
            stmt = line.rstrip(";").strip()
            if stmt.startswith("}"):
                remaining = stmt[1:].strip()
                if remaining.startswith("else{"):
                    # Handle } else{ on same line
                    self.builder.branch(cont_block)
                    self.builder.position_at_end(else_block)
                    i += 1  # move to else content
                    while i < len(lines):
                        stmt = lines[i].rstrip(";").strip()
                        if stmt == "}":
                            break
                        self._execute_statement(stmt)
                        i += 1
                    self.builder.branch(cont_block)
                    self.builder.position_at_end(cont_block)
                    i += 1  # skip else's }
                    return i
                else:
                    # No else
                    break
            self._execute_statement(stmt)
            i += 1
        self.builder.branch(cont_block)

        # === ELSE block (optional, for separate lines) ===
        self.builder.position_at_end(else_block)
        i += 1  # skip then's }
        if i < len(lines) and lines[i].strip() == "else{":
            i += 1  # move to else content
            while i < len(lines):
                stmt = lines[i].rstrip(";").strip()
                if stmt == "}":
                    break
                self._execute_statement(stmt)
                i += 1
            i += 1  # skip else's }
        self.builder.branch(cont_block)
        self.builder.position_at_end(cont_block)

        return i

    def _execute_statement(self, stmt: str):
        stmt = stmt.strip()
        if stmt.startswith("print("):
            inside = stmt[6:-1].strip()
            if inside.startswith('"') and inside.endswith('"'):
                self._print_string(inside[1:-1])
            else:
                self._print_value(self._eval_expr(inside))
        elif stmt.startswith("print "):
            arg = stmt[6:].strip()
            if arg.startswith('"') and arg.endswith('"'):
                self._print_string(arg[1:-1])
            else:
                self._print_value(self._eval_expr(arg))
        elif stmt.startswith("let "):
            self._parse_let(stmt[4:])
        else:
            self._eval_expr(stmt)

    # ------------------------------------------------------------------ printing
    def _print_string(self, text: str):
        text += "\n"
        data = text.encode("utf-8") + b"\0"
        name = f".str.{self._str_counter}"
        self._str_counter += 1
        gv = ir.GlobalVariable(self.module,
                               ir.ArrayType(ir.IntType(8), len(data)),
                               name=name)
        gv.linkage = "private"
        gv.global_constant = True
        gv.initializer = ir.Constant(gv.type.pointee,
                                     [ir.Constant(ir.IntType(8), b) for b in data])
        ptr = self.builder.gep(gv, [ir.Constant(ir.IntType(32), 0),
                                    ir.Constant(ir.IntType(32), 0)])
        self.builder.call(self.printf, [ptr])

    def _print_value(self, value: ir.Value):
        fmt = "%.10g\n"
        data = fmt.encode("utf-8") + b"\0"
        name = f".fmt.{self._str_counter}"
        self._str_counter += 1
        gv = ir.GlobalVariable(self.module,
                               ir.ArrayType(ir.IntType(8), len(data)),
                               name=name)
        gv.linkage = "private"
        gv.global_constant = True
        gv.initializer = ir.Constant(gv.type.pointee,
                                     [ir.Constant(ir.IntType(8), b) for b in data])
        ptr = self.builder.gep(gv, [ir.Constant(ir.IntType(32), 0),
                                    ir.Constant(ir.IntType(32), 0)])
        self.builder.call(self.printf, [ptr, value])

    # ------------------------------------------------------------------ expressions
    def _eval_condition(self, expr: str) -> ir.Value:
        expr = expr.replace(" ", "")
        ops = [">=", "<=", "==", "!=", ">", "<"]
        best_op = None
        best_pos = len(expr)
        for op in ops:
            pos = expr.find(op)
            if pos != -1 and pos < best_pos:
                best_op = op
                best_pos = pos

        if best_op is None:
            v = self._eval_expr(expr)
            return self.builder.fcmp_ordered("!=", v,
                    ir.Constant(ir.DoubleType(), 0.0))

        left = self._eval_expr(expr[:best_pos])
        right = self._eval_expr(expr[best_pos + len(best_op):])
        map_ = {">": "ogt", "<": "olt", ">=": "oge",
                "<=": "ole", "==": "oeq", "!=": "one"}
        return self.builder.fcmp_ordered(map_[best_op], left, right)

    def _parse_let(self, text: str):
        if "=" not in text:
            raise SyntaxError("let needs '='")
        name, expr = map(str.strip, text.split("=", 1))
        if not name.isidentifier():
            raise NameError(f"Bad identifier: {name}")
        value = self._eval_expr(expr)
        alloca = self.builder.alloca(ir.DoubleType(), name=name)
        self.builder.store(value, alloca)
        self.variables[name] = alloca

    def _eval_expr(self, expr: str) -> ir.Value:
        expr = expr.replace(" ", "")

        def parse_add(p=0):
            v, p = parse_mul(p)
            while p < len(expr) and expr[p] in "+-":
                op = expr[p]; p += 1
                r, p = parse_mul(p)
                v = self.builder.fadd(v, r) if op == "+" else self.builder.fsub(v, r)
            return v, p

        def parse_mul(p=0):
            v, p = parse_unary(p)
            while p < len(expr) and expr[p] in "*/":
                op = expr[p]; p += 1
                r, p = parse_unary(p)
                v = self.builder.fmul(v, r) if op == "*" else self.builder.fdiv(v, r)
            return v, p

        def parse_unary(p=0):
            if p < len(expr) and expr[p] == "-":
                v, p = parse_primary(p + 1)
                return self.builder.fneg(v), p
            return parse_primary(p)

        def parse_primary(p=0):
            if p >= len(expr):
                raise SyntaxError("Unexpected end")
            c = expr[p]
            if c == "(":
                v, p = parse_add(p + 1)
                if p >= len(expr) or expr[p] != ")":
                    raise SyntaxError("Missing ')'")
                return v, p + 1
            if c.isdigit() or c == ".":
                e = p
                while e < len(expr) and (expr[e].isdigit() or expr[e] == "."):
                    e += 1
                return ir.Constant(ir.DoubleType(), float(expr[p:e])), e
            if c.isalpha() or c == "_":
                e = p
                while e < len(expr) and (expr[e].isalnum() or expr[e] == "_"):
                    e += 1
                name = expr[p:e]
                if name not in self.variables:
                    raise NameError(f"Undefined: {name}")
                return self.builder.load(self.variables[name]), e
            raise SyntaxError(f"Unexpected char: {c!r}")

        v, pos = parse_add()
        if pos != len(expr):
            raise SyntaxError(f"Extra chars: {expr[pos:]}")
        return v


# ———————————————————— Test ————————————————————
if __name__ == "__main__":
    jit = TinyJIT()

    script = """
let a = 10;
let b = 20;

print(a);
print(b);

if(a > b){
    print("a > b");
}

if(a < b){
    print("a is less than b");
}

if(a <= 10){
    print("a <= 10 works");
} else{
    print("else branch");
}

if(a == 10){
    print("a equals 10");
}

if(a != 5){
    print("a is not 5");
}

let x = 3.14159;
if(x * x > 9){
    print("pi squared is greater than 9");
}
"""

    print("Running script:\n")
    print(script)
    print("Output:")
    print("=" * 50)
    jit.compile_and_run(script)