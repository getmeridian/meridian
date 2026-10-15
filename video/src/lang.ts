import { createContext, useContext } from "react";
import { COPY, type Lang } from "./copy";

export const LangContext = createContext<Lang>("en");
export const useLang = () => useContext(LangContext);
export const useCopy = () => COPY[useLang()];
